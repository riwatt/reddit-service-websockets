import signal

import gevent
import manhole

from baseplate import (
    config,
    metrics_client_from_config,
    error_reporter_from_config,
)
from baseplate.secrets import secrets_store_from_config
from raven.middleware import Sentry

from .dispatcher import MessageDispatcher
from .socketserver import SocketServer
from .source import MessageSource


manhole.install(oneshot_on='USR1')


CONFIG_SPEC = {
    "amqp": {
        "endpoint": config.Endpoint,
        "vhost": config.String,
        "username": config.String,
        "password": config.String,

        "exchange": {
            "broadcast": config.String,
            "status": config.String,
        },

        "send_status_messages": config.Boolean,
    },

    "web": {
        "ping_interval": config.Integer,
        "admin_auth": config.String,
        "conn_shed_rate": config.Integer,
    },
}


def make_app(raw_config):
    cfg = config.parse_config(raw_config, CONFIG_SPEC)

    metrics_client = metrics_client_from_config(raw_config)
    error_reporter = error_reporter_from_config(raw_config, __name__)
    secrets = secrets_store_from_config(raw_config)

    dispatcher = MessageDispatcher(metrics=metrics_client)

    source = MessageSource(
        config=cfg.amqp,
    )

    app = SocketServer(
        metrics=metrics_client,
        dispatcher=dispatcher,
        secrets=secrets,
        ping_interval=cfg.web.ping_interval,
        admin_auth=cfg.web.admin_auth,
        conn_shed_rate=cfg.web.conn_shed_rate,
    )

    # register SIGUSR2 to trigger app quiescing,
    #  useful if app processes are behind
    #  a process manager like einhorn.
    def _handle_quiesce_signal(_, frame):
        app._quiesce({}, bypass_auth=True)

    signal.signal(signal.SIGUSR2, _handle_quiesce_signal)
    signal.siginterrupt(signal.SIGUSR2, False)

    source.message_handler = dispatcher.on_message_received
    app.status_publisher = source.send_message

    gevent.spawn(source.pump_messages)

    # wrap the wsgi app with the raven middleware to publish exceptions back to
    # sentry (since we don't have proper baseplate spans here)
    app = Sentry(app, error_reporter)

    return app

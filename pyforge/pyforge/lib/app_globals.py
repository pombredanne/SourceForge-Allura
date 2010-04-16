# -*- coding: utf-8 -*-

"""The application's Globals object"""

__all__ = ['Globals']
import logging
import socket
import re
from urllib import urlencode
from ConfigParser import RawConfigParser
from collections import defaultdict

import pkg_resources

from tg import config, session
from pylons import c, request
import pysolr
import oembed
import markdown
from carrot.connection import BrokerConnection
from carrot.messaging import Publisher
from pymongo.bson import ObjectId
from paste.deploy.converters import asint, asbool

from pyforge import model as M
from pyforge.lib.markdown_extensions import ForgeExtension

from pyforge.lib import gravatar

log = logging.getLogger(__name__)

class Globals(object):
    """Container for objects available throughout the life of the application.

    One instance of Globals is created during application initialization and
    is available during requests via the 'app_globals' variable.

    """

    def __init__(self):
        """Do nothing, by default."""
        self.pyforge_templates = pkg_resources.resource_filename('pyforge', 'templates')

        # Setup SOLR
        self.solr_server = config.get('solr.server')
        if self.solr_server:
            self.solr =  pysolr.Solr(self.solr_server)
        else: # pragma no cover
            self.solr = None
        self.use_queue = asbool(config.get('use_queue', False))

        # Setup RabbitMQ
        if asbool(config.get('amqp.mock')):
            self.mock_amq = MockAMQ()
            self._publish = self.mock_amq.publish
        else:
            self.conn = BrokerConnection(
                hostname=config.get('amqp.hostname', 'localhost'),
                port=asint(config.get('amqp.port', 5672)),
                userid=config.get('amqp.userid', 'testuser'),
                password=config.get('amqp.password', 'testpw'),
                virtual_host=config.get('amqp.vhost', 'testvhost'))
            self.publisher = dict(
                audit=Publisher(connection=self.conn, exchange='audit', auto_declare=False),
                react=Publisher(connection=self.conn, exchange='react', auto_declare=False))

        # Setup markdown
        self.markdown = markdown.Markdown(
            extensions=['codehilite', ForgeExtension(), 'meta'],
            output_format='html4')
        self.markdown_wiki = markdown.Markdown(
            extensions=['codehilite', ForgeExtension(wiki=True),'meta'],
            output_format='html4')

        # Setup OEmbed
        cp = RawConfigParser()
        cp.read(config['oembed.config'])
        self.oembed_consumer = consumer = oembed.OEmbedConsumer()
        for endpoint in cp.sections():
            values = [ v for k,v in cp.items(endpoint) ]
            consumer.addEndpoint(oembed.OEmbedEndpoint(endpoint, values))

        # Setup Gravatar
        self.gravatar = gravatar.url

        self.oid_store = M.OpenIdStore()

    def oid_session(self):
        if 'openid_info' in session:
            return session['openid_info']
        else:
            session['openid_info'] = result = {}
            session.save()
            return result
        
    def app_static(self, resource, app=None):
        app = app or c.app
        return ''.join(
            [ config['static_root'],
              app.config.plugin_name,
              '/',
              resource ])

    def set_project(self, pid):
        c.project = M.Project.query.get(shortname=pid, deleted=False)

    def set_app(self, name):
        c.app = c.project.app_instance(name)

    def publish(self, xn, key, message=None, **kw):
        project = getattr(c, 'project', None)
        app = getattr(c, 'app', None)
        user = getattr(c, 'user', None)
        if message is None: message = {}
        if project:
            message.setdefault('project_id', project._id)
        if app:
            message.setdefault('mount_point', app.config.options.mount_point)
        if user:
            if user._id is None:
                message.setdefault('user_id',  None)
            else:
                message.setdefault('user_id',  user._id)
        # Make message safe for serialization
        if kw.get('serializer', 'json') in ('json', 'yaml'):
            for k, v in message.items():
                if isinstance(v, ObjectId):
                    message[k] = str(v)
        if getattr(c, 'queued_messages', None) is not None:
            c.queued_messages.append(dict(
                    xn=xn,
                    message=message,
                    routing_key=key,
                    **kw))
        else:
            self._publish(xn, message, routing_key=key, **kw)

    def _publish(self, xn, message, routing_key, **kw):
        try:
            self.publisher[xn].send(message, routing_key=routing_key, **kw)
        except socket.error: # pragma no cover
            return
            log.exception('''Failure publishing message:
xn         : %r
routing_key: %r
data       : %r
''', xn, routing_key, message)

    def url(self, base, **kw):
        params = urlencode(kw)
        if params:
            return '%s://%s%s?%s' % (request.scheme, request.host, base, params)
        else:
            return '%s://%s%s' % (request.scheme, request.host, base)

class MockAMQ(object):

    def __init__(self):
        self.exchanges = defaultdict(list)
        self.queue_bindings = defaultdict(list)

    def publish(self, xn, message, routing_key, **kw):
        self.exchanges[xn].append(
            dict(routing_key=routing_key, message=message, kw=kw))

    def pop(self, xn):
        return self.exchanges[xn].pop(0)

    def setup_handlers(self):
        from pyforge.command.reactor import plugin_consumers, ReactorCommand
        from pyforge.command import base
        base.log = logging.getLogger('pyforge.command')
        base.M = M
        self.plugins = [
            (ep.name, ep.load()) for ep in pkg_resources.iter_entry_points('pyforge') ]
        self.reactor = ReactorCommand('reactor_setup')
        self.reactor.parse_args([])
        for name, plugin in self.plugins:
            for method, xn, qn, keys in plugin_consumers(name, plugin):
                for k in keys:
                    self.queue_bindings[xn].append(
                        dict(key=k, plugin_name=name, method=method))
            # self.setup_plugin(name, plugin)

    def handle(self, xn):
        msg = self.pop(xn)
        for handler in self.queue_bindings[xn]:
            if self._route_matches(handler['key'], msg['routing_key']):
                self._route(xn, msg, handler['plugin_name'], handler['method'])

    def handle_all(self):
        for xn, messages in self.exchanges.items():
            while messages:
                self.handle(xn)

    def _route(self, xn, msg, plugin_name, method):
        import mock
        if xn == 'audit':
            callback = self.reactor.route_audit(plugin_name, method)
        else:
            callback = self.reactor.route_react(plugin_name, method)
        data = msg['message']
        message = mock.Mock()
        message.delivery_info = dict(
            routing_key=msg['routing_key'])
        message.ack = lambda:None
        return callback(data, message)

    def _route_matches(self, pattern, key):
        re_pattern = (pattern
                      .replace('.', r'\.')
                      .replace('*', r'(?:\w+)')
                      .replace('#', r'(?:\w+)(?:\.\w+)*'))
        return re.match(re_pattern+'$', key)



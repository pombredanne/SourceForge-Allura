import difflib
import logging
from pprint import pformat

import pkg_resources
from pylons import c, request
from tg import expose, redirect, flash
from tg.decorators import with_trailing_slash
from webob import exc


from allura import version
from allura.app import Application, WidgetController, ConfigOption, SitemapEntry
from allura.lib import helpers as h
from allura.controllers import BaseController
from allura.ext.project_home import model as M
from allura import model
from allura.lib.security import require, require_access
from allura.lib.decorators import require_post
from allura.lib.widgets.project_list import ProjectScreenshots

log = logging.getLogger(__name__)

class W:
    screenshot_list = ProjectScreenshots()

class ProjectWidgets(WidgetController):
    widgets=['welcome']

    def __init__(self, app): pass

    def welcome(self):
        return self.portlet('<p><!-- Please configure your widgets --></p>')

class ProjectHomeApp(Application):
    __version__ = version.__version__
    widget = ProjectWidgets
    installable = False
    tool_label = 'home'
    default_mount_label='Project Home'
    icons={
        24:'images/home_24.png',
        32:'images/home_32.png',
        48:'images/home_48.png'
    }

    def __init__(self, project, config):
        Application.__init__(self, project, config)
        self.root = ProjectHomeController()
        self.api_root = RootRestController()
        self.templates = pkg_resources.resource_filename(
            'allura.ext.project_home', 'templates')

    def is_visible_to(self, user):
        '''Whether the user can view the app.'''
        return True

    def main_menu(self):
        '''Apps should provide their entries to be added to the main nav
        :return: a list of :class:`SitemapEntries <allura.app.SitemapEntry>`
        '''
        return [ SitemapEntry(
                self.config.options.mount_label.title(),
                '.')]


    @property
    @h.exceptionless([], log)
    def sitemap(self):
        menu_id = 'Home'
        return [
            SitemapEntry('Home', c.project.url()) ]

    @h.exceptionless([], log)
    def sidebar_menu(self):
        return [ SitemapEntry('Configure', 'configuration')]

    def admin_menu(self):
        return []

    def install(self, project):
        super(ProjectHomeApp, self).install(project)
        pr = c.user.project_role()
        if pr:
            self.config.acl = [
                model.ACE.allow(pr._id, perm)
                for perm in self.permissions ]

class ProjectHomeController(BaseController):

    def _check_security(self):
        require_access(c.project, 'read')

    @with_trailing_slash
    @expose('jinja:allura.ext.project_home:templates/project_index.html')
    def index(self, **kw):
        config = M.PortalConfig.current()
        c.screenshot_list = W.screenshot_list
        return dict(
            layout_class=config.layout_class,
            layout=config.rendered_layout())

    @expose('jinja:allura.ext.project_home:templates/project_dashboard_configuration.html')
    def configuration(self):
        config = M.PortalConfig.current()
        mount_points = [
            (ac.options.mount_point, ac.load())
            for ac in c.project.app_configs ]
        widget_types = [
            dict(mount_point=mp, widget_name=w)
            for mp, app_class in mount_points
            for w in app_class.widget.widgets ]
        return dict(
            layout_class=config.layout_class,
            layout=config.layout,
            widget_types=widget_types)

    @h.vardec
    @expose()
    @require_post()
    def update_configuration(self, divs=None, layout_class=None, new_div=None, **kw):
        require_access(c.project, 'update')
        config = M.PortalConfig.current()
        config.layout_class = layout_class
        # Handle updated and deleted divs
        if divs is None: divs = []
        new_divs = []
        for div in divs:
            log.info('Got div update:%s', pformat(div))
            if div.get('del'): continue
            new_divs.append(div)
        # Handle new divs
        if new_div:
            new_divs.append(dict(name=h.nonce(), content=[]))
        config.layout = []
        for div in new_divs:
            content = []
            for w in div.get('content', []):
                if w.get('del'): continue
                mp,wn = w['widget'].split('/')
                content.append(dict(mount_point=mp, widget_name=wn))
            if div.get('new_widget'):
                content.append(dict(mount_point='home', widget_name='welcome'))
            config.layout.append(dict(
                    name=div['name'],
                    content=content))
        redirect('configuration')


class RootRestController(BaseController):

    def _check_security(self):
        require_access(c.project, 'read')

    @expose('json:')
    def index(self, **kwargs):
        return dict(shortname=c.project.shortname)

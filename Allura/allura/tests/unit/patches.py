from mock import Mock, patch
from pylons import c

from allura.tests.unit.factories import create_project, create_app_config


def fake_app_patch(test_case):
    project = create_project('myproject')
    app_config = create_app_config(project, 'my_app')
    app = Mock()
    app.__version__ = '0'
    app.config = app_config
    app.url = '/-app-/'
    return patch.object(c, 'app', app, create=True)


def project_app_loading_patch(test_case):
    test_case.fake_app = Mock()
    test_case.project_app_instance_function = Mock()
    test_case.project_app_instance_function.return_value = test_case.fake_app

    return patch('allura.model.project.Project.app_instance',
                 test_case.project_app_instance_function)


def disable_notifications_patch(test_case):
    return patch('allura.model.notification.Notification.post')


def fake_redirect_patch(test_case):
    return patch('allura.controllers.discuss.redirect')


def fake_request_patch(test_case):
    return patch('allura.controllers.discuss.request')


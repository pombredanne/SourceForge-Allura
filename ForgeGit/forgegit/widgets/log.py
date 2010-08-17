import ew
from allura.lib.widgets import form_fields as ffw

class GitLog(ew.SimpleForm):
    template='genshi:forgegit.widgets.templates.log'
    params=['value', 'limit', 'page', 'count', 'show_paging']
    value=None
    limit=None
    page=0
    count=0
    show_paging=True

    class fields(ew.WidgetsList):
        page_list=ffw.PageList()
        page_size=ffw.PageSize()
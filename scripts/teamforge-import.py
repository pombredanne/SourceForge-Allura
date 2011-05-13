import logging
from getpass import getpass
from optparse import OptionParser
import re
import os
import os.path
from time import mktime
import json

from urlparse import urlparse
from urllib import FancyURLopener

from suds.client import Client
from suds import WebFault

log = logging.getLogger(__file__)

'''

http://help.collab.net/index.jsp?topic=/teamforge520/reference/api-services.html

http://www.open.collab.net/nonav/community/cif/csfe/50/javadoc/index.html?com/collabnet/ce/soap50/webservices/page/package-summary.html

'''

options = None
s = None # security token

def make_client(api_url, app):
    return Client(api_url + app + '?wsdl', location=api_url + app)

def main():
    global options, s
    optparser = OptionParser(usage='''%prog [--options] [projID projID projID]\nIf no project ids are given, all projects will be migrated''')
    optparser.add_option('--api-url', dest='api_url', help='e.g. https://hostname/ce-soap50/services/')
    optparser.add_option('--attachment-url', dest='attachment_url', default='/sf/%s/do/%s/')
    optparser.add_option('--default-wiki-text', dest='default_wiki_text', default='PRODUCT NAME HERE', help='used in determining if a wiki page text is default or changed')
    optparser.add_option('-u', '--username', dest='username')
    optparser.add_option('-p', '--password', dest='password')
    optparser.add_option('-o', '--output-dir', dest='output_dir', default='teamforge-export/')
    optparser.add_option('--list-project-ids', action='store_true', dest='list_project_ids')
    options, project_ids = optparser.parse_args()


    c = make_client(options.api_url, 'CollabNet')
    api_v = c.service.getApiVersion()
    if not api_v.startswith('5.4.'):
        log.warning('Unexpected API Version %s.  May not work correctly.' % api_v)


    s = c.service.login(options.username, options.password or getpass('Password: '))
    teamforge_v = c.service.getVersion(s)
    if not teamforge_v.startswith('5.4.'):
        log.warning('Unexpected TeamForge Version %s.  May not work correctly.' % teamforge_v)


    if not project_ids:
        projects = c.service.getProjectList(s)
        project_ids = [p.id for p in projects.dataRows]

    if options.list_project_ids:
        print ' '.join(project_ids)
        return

    if not os.path.exists(options.output_dir):
        os.makedirs(options.output_dir)
    for pid in project_ids:
        project = c.service.getProjectData(s, pid)
        project.shortname = project.path.split('.')[-1]
        log.info('Project: %s %s %s' % (project.id, project.title, project.path))

        out_dir = os.path.join(options.output_dir, project.id)
        if not os.path.exists(out_dir):
            os.mkdir(out_dir)

        get_files(project)
        get_homepage_wiki(project)
        check_unsupported_tools(project)

def check_unsupported_tools(project):
    docs = make_client(options.api_url, 'DocumentApp')
    doc_count = 0
    for doc in docs.service.getDocumentFolderList(s, project.id, recursive=True).dataRows:
        if doc.title == 'Root Folder':
            continue
        doc_count += 1
    if doc_count:
        log.warn('Migrating documents is not supported, but found %s docs' % doc_count)

    scm = make_client(options.api_url, 'ScmApp')
    for repo in scm.service.getRepositoryList(s, project.id).dataRows:
        log.warn('Migrating SCM repos is not supported, but found %s' % repo.repositoryPath)

    tasks = make_client(options.api_url, 'TaskApp')
    task_count = len(tasks.service.getTaskList(s, project.id, filters=None).dataRows)
    if task_count:
        log.warn('Migrating tasks is not supported, but found %s tasks' % task_count)

    tracker = make_client(options.api_url, 'TrackerApp')
    tracker_count = len(tracker.service.getArtifactList(s, project.id, filters=None).dataRows)
    if tracker_count:
        log.warn('Migrating trackers is not supported, but found %s tracker artifacts' % task_count)


def save(content, project, *paths):
    out_file = os.path.join(options.output_dir, project.id, *paths)
    if not os.path.exists(os.path.dirname(out_file)):
        os.makedirs(os.path.dirname(out_file))
    with open(out_file, 'w') as out:
        out.write(content)

class StatusCheckingURLopener(FancyURLopener):
  def http_error_default(self, url, fp, errcode, errmsg, headers):
        raise Exception(errcode)
statusCheckingURLopener = StatusCheckingURLopener()

def download_file(tool, url_path, *filepaths):
    if tool == 'wiki':
        action = 'viewAttachment'
    elif tool == 'frs':
        action = 'downloadFile'
    else:
        raise ValueError('tool %s not supported' % tool)
    action_url = options.attachment_url % (tool, action)

    out_file = os.path.join(options.output_dir, *filepaths)
    if not os.path.exists(os.path.dirname(out_file)):
        os.makedirs(os.path.dirname(out_file))

    hostname = urlparse(options.api_url).hostname
    scheme = urlparse(options.api_url).scheme
    url = scheme + '://' + hostname + action_url + url_path
    log.debug('fetching %s' % url)
    statusCheckingURLopener.retrieve(url, out_file)
    return out_file

bracket_macro = re.compile(r'\[(.*?)\]')
h1 = re.compile(r'^!!!', re.MULTILINE)
h2 = re.compile(r'^!!', re.MULTILINE)
h3 = re.compile(r'^!', re.MULTILINE)
def wiki2markdown(markup):
    '''
    Partial implementation of http://help.collab.net/index.jsp?topic=/teamforge520/reference/wiki-wikisyntax.html
    '''
    def bracket_handler(matchobj):
        snippet = matchobj.group(1)
        # TODO: support [foo|bar.jpg]
        if snippet.startswith('sf:'):
            # can't handle these macros
            return matchobj.group(0)
        elif snippet.endswith('.jpg') or snippet.endswith('.gif') or snippet.endswith('.png'):
            filename = snippet.split('/')[-1]
            return '[[img src=%s]]' % filename
        elif '|' in snippet:
            text, link = snippet.split('|', 1)
            return '[%s](%s)' % (text, link)
        else:
            # regular link
            return '<%s>' % snippet
    markup = bracket_macro.sub(bracket_handler, markup)
    markup = h1.sub('#', markup)
    markup = h2.sub('##', markup)
    markup = h3.sub('###', markup)
    return markup

def find_image_references(markup):
    'yields filenames'
    for matchobj in bracket_macro.finditer(markup):
        snippet = matchobj.group(1)
        if snippet.endswith('.jpg') or snippet.endswith('.gif') or snippet.endswith('.png'):
            yield snippet

def get_homepage_wiki(project):
    '''
    Extracts home page and wiki pages
    '''
    wiki = make_client(options.api_url, 'WikiApp')

    pages = {}
    wiki_pages = wiki.service.getWikiPageList(s, project.id)
    for wiki_page in wiki_pages.dataRows:
        wiki_page = wiki.service.getWikiPageData(s, wiki_page.id)
        pagename = wiki_page.path.split('/')[-1]
        save(json.dumps(dict(wiki_page), default=str), project, 'wiki', pagename+'.json')
        if not wiki_page.wikiText:
            log.debug('skip blank wiki page %s' % wiki_page.path)
            continue
        pages[pagename] = wiki_page.wikiText

    # PageApp does not provide a useful way to determine the Project Home special wiki page
    # so use some heuristics
    homepage = None
    if '$ProjectHome' in pages and options.default_wiki_text not in pages['$ProjectHome']:
        homepage = pages.pop('$ProjectHome')
    elif 'HomePage' in pages and options.default_wiki_text not in pages['HomePage']:
        homepage = pages.pop('HomePage')
    elif '$ProjectHome' in pages:
        homepage = pages.pop('$ProjectHome')
    elif 'HomePage' in pages:
        homepage = pages.pop('HomePage')
    else:
        log.warn('did not find homepage')

    if homepage:
        save(homepage, project, 'wiki', 'homepage.markdown')
        for img_ref in find_image_references(homepage):
            filename = img_ref.split('/')[-1]
            download_file('wiki', project.path + '/wiki/' + img_ref, project.id, 'homepage', filename)

    for path, text in pages.iteritems():
        if options.default_wiki_text in text:
            log.debug('skipping default wiki page %s' % path)
        else:
            save(text, project, 'wiki', path+'.markdown')

def get_files(project):
    frs = make_client(options.api_url, 'FrsApp')
    valid_pfs_filename = re.compile(r'(?![. ])[-_ +.,=#~@!()\[\]a-zA-Z0-9]+(?<! )$')
    pfs_output_dir = os.path.join(os.path.abspath(options.output_dir), 'PFS', project.shortname)

    def handle_path(obj, prev_path):
        path_component = obj.title.strip().replace('/', ' ').replace('&','').replace(':','')
        path = os.path.join(prev_path, path_component)
        if not valid_pfs_filename.match(path_component):
            log.error('Invalid filename: "%s"' % path)
        save(json.dumps(dict(obj), default=str),
            project, 'frs', path+'.json')
        return path

    for pkg in frs.service.getPackageList(s, project.id).dataRows:
        pkg_path = handle_path(pkg, '')

        for rel in frs.service.getReleaseList(s, pkg.id).dataRows:
            rel_path = handle_path(rel, pkg_path)

            for file in frs.service.getFrsFileList(s, rel.id).dataRows:
                details = frs.service.getFrsFileData(s, file.id)

                file_path = os.path.join(rel_path, file.title.strip())
                save(json.dumps(dict(file,
                                     lastModifiedBy=details.lastModifiedBy,
                                     lastModifiedDate=details.lastModifiedDate,
                                     ),
                                default=str),
                     project,
                     'frs',
                     file_path+'.json'
                     )
                #'''
                download_file('frs', rel.path + '/' + file.id, pfs_output_dir, file_path)
                # TODO: createdOn
                mtime = int(mktime(details.lastModifiedDate.timetuple()))
                os.utime(os.path.join(pfs_output_dir, file_path), (mtime, mtime))

                # now set mtime on the way back up the tree (so it isn't clobbered):

            # TODO: createdOn
            mtime = int(mktime(rel.lastModifiedOn.timetuple()))
            os.utime(os.path.join(pfs_output_dir, rel_path), (mtime, mtime))
        # TODO: createdOn
        mtime = int(mktime(pkg.lastModifiedOn.timetuple()))
        os.utime(os.path.join(pfs_output_dir, pkg_path), (mtime, mtime))
                #'''

'''
print c.service.getProjectData(s, p.id)
print c.service.getProjectAccessLevel(s, p.id)
print c.service.listProjectAdmins(s, p.id)

for forum in discussion.service.getForumList(s, p.id).dataRows:
    print forum.title
    for topic in discussion.service.getTopicList(s, forum.id).dataRows:
        print '  ', topic.title
        for post in discussion.service.getPostList(s, topic.id).dataRows:
            print '    ', post.title, post.createdDate, post.createdByUserName
            print post.content
            print
            break
        break
    break

print news.service.getNewsPostList(s, p.id)
break
'''

if __name__ == '__main__':
    logging.basicConfig(level=logging.WARN)
    log.setLevel(logging.DEBUG)
    main()


def test_convert_markup():

    markup = '''
!this is the first headline
Please note that this project is for distributing, discussing, and supporting the open source software we release.

[http://www.google.com]

[SourceForge |http://www.sf.net]

[$ProjectHome/myimage.jpg]
[$ProjectHome/anotherimage.jpg]

!!! Project Statistics

|[sf:frsStatistics]|[sf:artifactStatistics]|
    '''

    new_markup = wiki2markdown(markup)
    assert '\n[[img src=myimage.jpg]]\n[[img src=anotherimage.jpg]]\n' in new_markup
    assert '\n###this is the first' in new_markup
    assert '\n# Project Statistics' in new_markup
    assert '<http://www.google.com>' in new_markup
    assert '[SourceForge ](http://www.sf.net)' in new_markup
    assert '[sf:frsStatistics]' in new_markup

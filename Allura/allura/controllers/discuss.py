from mimetypes import guess_type
from urllib import unquote
from datetime import datetime

import tg
from tg import expose, redirect, validate, request, response, flash
from tg.decorators import before_validate, with_trailing_slash, without_trailing_slash
from pylons import g, c
from formencode import validators
from webob import exc

from ming.base import Object
from ming.utils import LazyProperty

from allura import model as model
from base import BaseController
from allura.lib import helpers as h
from allura.lib.security import require, has_artifact_access
from allura.lib.helpers import DateTimeConverter

from allura.lib.widgets import discuss as DW

class pass_validator(object):
    def validate(self, v, s):
        return v
pass_validator=pass_validator()

class ModelConfig(object):
    Discussion=model.Discussion
    Thread=model.Thread
    Post=model.Post
    Attachment=model.Attachment

class WidgetConfig(object):
    # Forms
    subscription_form = DW.SubscriptionForm()
    edit_post = DW.EditPost()
    moderate_thread = DW.ModerateThread()
    moderate_post = DW.ModeratePost()
    flag_post = DW.FlagPost()
    post_filter = DW.PostFilter()
    moderate_posts=DW.ModeratePosts()
    # Other widgets
    discussion = DW.Discussion()
    thread = DW.Thread()
    post = DW.Post()
    thread_header = DW.ThreadHeader()

# Controllers
class DiscussionController(BaseController):
    M=ModelConfig
    W=WidgetConfig

    def __init__(self):
        self.thread = ThreadsController(self)
        self.attachment = AttachmentsController(self)
        self.moderate = ModerationController(self)
        if not hasattr(self, 'ThreadController'):
            self.ThreadController = ThreadController
        if not hasattr(self, 'PostController'):
            self.PostController = PostController
        if not hasattr(self, 'AttachmentController'):
            self.AttachmentController = AttachmentController

    @expose('allura.templates.discussion.index')
    def index(self, threads=None, limit=None, page=0, count=0, **kw):
        c.discussion = self.W.discussion
        if threads is None:
            threads = self.discussion.threads
        return dict(discussion=self.discussion, limit=limit, page=page, count=count, threads=threads)

    @h.vardec
    @expose()
    @validate(pass_validator, error_handler=index)
    def subscribe(self, **kw):
        threads = kw.pop('threads')
        for t in threads:
            thread = self.M.Thread.query.find(dict(_id=t['_id'])).first()
            if 'subscription' in t:
                thread['subscription'] = True
            else:
                thread['subscription'] = False
        redirect(request.referer)

class AppDiscussionController(DiscussionController):

    @LazyProperty
    def discussion(self):
        return self.M.Discussion.query.get(shortname=c.app.config.options.mount_point)

class ThreadsController(BaseController):
    __metaclass__=h.ProxiedAttrMeta
    M=h.attrproxy('_discussion_controller', 'M')
    W=h.attrproxy('_discussion_controller', 'W')
    ThreadController=h.attrproxy('_discussion_controller', 'ThreadController')
    PostController=h.attrproxy('_discussion_controller', 'PostController')
    AttachmentController=h.attrproxy('_discussion_controller', 'AttachmentController')

    def __init__(self, discussion_controller):
        self._discussion_controller = discussion_controller

    @expose()
    def _lookup(self, id=None, *remainder):
        if id:
            id=unquote(id)
            return self.ThreadController(self._discussion_controller, id), remainder
        else:
            raise exc.HTTPNotFound()

class ThreadController(BaseController):
    __metaclass__=h.ProxiedAttrMeta
    M=h.attrproxy('_discussion_controller', 'M')
    W=h.attrproxy('_discussion_controller', 'W')
    ThreadController=h.attrproxy('_discussion_controller', 'ThreadController')
    PostController=h.attrproxy('_discussion_controller', 'PostController')
    AttachmentController=h.attrproxy('_discussion_controller', 'AttachmentController')

    def _check_security(self):
        require(has_artifact_access('read', self.thread))

    def __init__(self, discussion_controller, thread_id):
        self._discussion_controller = discussion_controller
        self.discussion = discussion_controller.discussion
        self.thread = self.M.Thread.query.get(_id=thread_id)

    @expose()
    def _lookup(self, id, *remainder):
        id=unquote(id)
        return self.PostController(self._discussion_controller, self.thread, id), remainder

    @expose('allura.templates.discussion.thread')
    def index(self, limit=None, page=0, count=0, **kw):
        c.thread = self.W.thread
        c.thread_header = self.W.thread_header
        limit, page, start = g.handle_paging(limit, page)
        self.thread.num_views += 1
        count = self.thread.query_posts(page=page, limit=int(limit)).count()
        return dict(discussion=self.thread.discussion,
                    thread=self.thread,
                    page=page,
                    count=count,
                    limit=limit)

    @h.vardec
    @expose()
    @validate(pass_validator, error_handler=index)
    def post(self, **kw):
        require(has_artifact_access('post', self.thread))
        kw = self.W.edit_post.validate(kw, None)
        p = self.thread.add_post(**kw)
        if self.thread.artifact:
            self.thread.artifact.mod_date = datetime.now()
        flash('Message posted')
        redirect(request.referer)

    @expose()
    def tag(self, labels, **kw):
        require(has_artifact_access('post', self.thread))
        self.thread.labels = labels.split(',')
        redirect(request.referer)

    @expose()
    def flag_as_spam(self, **kw):
        require(has_artifact_access('moderate', self.thread))
        self.thread.first_post.status='spam'
        flash('Thread flagged as spam.')
        redirect(request.referer)

    @without_trailing_slash
    @expose()
    @validate(dict(
            since=DateTimeConverter(if_empty=None),
            until=DateTimeConverter(if_empty=None),
            page=validators.Int(if_empty=None),
            limit=validators.Int(if_empty=None)))
    def feed(self, since=None, until=None, page=None, limit=None):
        if request.environ['PATH_INFO'].endswith('.atom'):
            feed_type = 'atom'
        else:
            feed_type = 'rss'
        title = 'Recent posts to %s' % (self.thread.subject or '(no subject)')
        feed = model.Feed.feed(
            {'artifact_reference':self.thread.dump_ref()},
            feed_type,
            title,
            self.thread.url(),
            title,
            since, until, page, limit)
        response.headers['Content-Type'] = ''
        response.content_type = 'application/xml'
        return feed.writeString('utf-8')

class PostController(BaseController):
    __metaclass__=h.ProxiedAttrMeta
    M=h.attrproxy('_discussion_controller', 'M')
    W=h.attrproxy('_discussion_controller', 'W')
    ThreadController=h.attrproxy('_discussion_controller', 'ThreadController')
    PostController=h.attrproxy('_discussion_controller', 'PostController')
    AttachmentController=h.attrproxy('_discussion_controller', 'AttachmentController')

    def _check_security(self):
        require(has_artifact_access('read', self.post))

    def __init__(self, discussion_controller, thread, slug):
        self._discussion_controller = discussion_controller
        self.thread = thread
        self._post_slug = slug

    @LazyProperty
    def post(self):
        result = self.M.Post.query.find(dict(slug=self._post_slug)).all()
        for p in result:
            if p.thread_id == self.thread._id: return p
        if result:
            redirect(result[0].url())
        else:
            redirect('..')

    @h.vardec
    @expose('allura.templates.discussion.post')
    @validate(pass_validator)
    def index(self, version=None, **kw):
        c.post = self.W.post
        if request.method == 'POST':
            require(has_artifact_access('moderate', self.post))
            post_fields = self.W.edit_post.validate(kw, None)
            for k,v in post_fields.iteritems():
                try:
                    setattr(self.post, k, v)
                except AttributeError:
                    continue
            redirect(request.referer)
        elif request.method=='GET':
            if version is not None:
                HC = self.post.__mongometa__.history_class
                ss = HC.query.find({'artifact_id':self.post._id, 'version':int(version)}).first()
                if not ss: raise exc.HTTPNotFound
                post = Object(
                    ss.data,
                    acl=self.post.acl,
                    author=self.post.author,
                    url=self.post.url,
                    thread=self.post.thread,
                    reply_subject=self.post.reply_subject,
                    attachments=self.post.attachments,
                    )
            else:
                post=self.post
            return dict(discussion=self.post.discussion,
                        post=post)

    @h.vardec
    @expose()
    @validate(pass_validator, error_handler=index)
    def reply(self, **kw):
        require(has_artifact_access('post', self.thread))
        kw = self.W.edit_post.validate(kw, None)
        self.thread.post(parent_id=self.post._id, **kw)
        self.thread.num_replies += 1
        redirect(request.referer)

    @h.vardec
    @expose()
    @validate(pass_validator, error_handler=index)
    def moderate(self, **kw):
        require(has_artifact_access('moderate', self.post.thread))
        if kw.pop('delete', None):
            self.post.delete()
            self.thread.update_stats()
        elif kw.pop('spam', None):
            self.post.status = 'spam'
            self.thread.update_stats()
        redirect(request.referer)

    @h.vardec
    @expose()
    @validate(pass_validator, error_handler=index)
    def flag(self, **kw):
        self.W.flag_post.validate(kw, None)
        if c.user._id not in self.post.flagged_by:
            self.post.flagged_by.append(c.user._id)
            self.post.flags += 1
        redirect(request.referer)

    @h.vardec
    @expose()
    def attach(self, file_info=None):
        require(has_artifact_access('moderate', self.post))
        if file_info is not None:
            filename = file_info.filename
            content_type = guess_type(filename)
            if content_type[0]: content_type = content_type[0]
            else: content_type = 'application/octet-stream'
            with self.M.Attachment.create(
                content_type=content_type,
                filename=filename,
                discussion_id=self.post.discussion._id,
                post_id=self.post._id) as fp:
                while True:
                    s = file_info.file.read()
                    if not s: break
                    fp.write(s)
            redirect(request.referer)
        else:
            redirect('../')

    @expose()
    def _lookup(self, id, *remainder):
        id=unquote(id)
        return self.PostController(
            self._discussion_controller,
            self.thread, self._post_slug + '/' + id), remainder

class AttachmentsController(BaseController):
    __metaclass__=h.ProxiedAttrMeta
    M=h.attrproxy('_discussion_controller', 'M')
    W=h.attrproxy('_discussion_controller', 'W')
    ThreadController=h.attrproxy('_discussion_controller', 'ThreadController')
    PostController=h.attrproxy('_discussion_controller', 'PostController')
    AttachmentController=h.attrproxy('_discussion_controller', 'AttachmentController')


    def __init__(self, discussion_controller):
        self._discussion_controller = discussion_controller

    @expose()
    def _lookup(self, filename, *args):
        filename=unquote(filename)
        return self.AttachmentController(self._discussion_controller, filename), args

class AttachmentController(BaseController):
    __metaclass__=h.ProxiedAttrMeta
    M=h.attrproxy('_discussion_controller', 'M')
    W=h.attrproxy('_discussion_controller', 'W')
    ThreadController=h.attrproxy('_discussion_controller', 'ThreadController')
    PostController=h.attrproxy('_discussion_controller', 'PostController')
    AttachmentController=h.attrproxy('_discussion_controller', 'AttachmentController')

    def _check_security(self):
        require(has_artifact_access('read', self.post))

    def __init__(self, discussion_controller, filename):
        self._discussion_controller = discussion_controller
        self.filename = filename
        self.attachment = self.M.Attachment.query.get(filename=filename)
        self.post = self.attachment.post

    @expose()
    def index(self, delete=False, embed=True, **kw):
        if request.method == 'POST':
            require(has_artifact_access('moderate', self.post))
            if delete: self.attachment.delete()
            redirect(request.referer)
        with self.attachment.open() as fp:
            filename = fp.metadata['filename'].encode('utf-8')
            response.headers['Content-Type'] = ''
            response.content_type = fp.content_type.encode('utf-8')
            if not embed:
                response.headers.add('Content-Disposition',
                                     'attachment;filename=%s' % filename)
            return fp.read()


class ModerationController(BaseController):
    __metaclass__=h.ProxiedAttrMeta
    M=h.attrproxy('_discussion_controller', 'M')
    W=h.attrproxy('_discussion_controller', 'W')
    ThreadController=h.attrproxy('_discussion_controller', 'ThreadController')
    PostController=h.attrproxy('_discussion_controller', 'PostController')
    AttachmentController=h.attrproxy('_discussion_controller', 'AttachmentController')

    def _check_security(self):
        require(has_artifact_access('moderate', self.discussion))

    def __init__(self, discussion_controller):
        self._discussion_controller = discussion_controller

    @LazyProperty
    def discussion(self):
        return self._discussion_controller.discussion

    @h.vardec
    @expose('allura.templates.discussion.moderate')
    @validate(pass_validator)
    def index(self, **kw):
        kw = WidgetConfig.post_filter.validate(kw, None)
        page = kw.pop('page', 0)
        limit = kw.pop('limit', 50)
        status = kw.pop('status', '-')
        flag = kw.pop('flag', None)
        c.post_filter = WidgetConfig.post_filter
        c.moderate_posts = WidgetConfig.moderate_posts
        query = dict(
            discussion_id=self.discussion._id)
        if status != '-':
            query['status'] = status
        if flag:
            query['flags'] = {'$gte': int(flag) }
        q = model.Post.query.find(query)
        count = q.count()
        page = int(page)
        limit = int(limit)
        q = q.skip(page)
        q = q.limit(limit)
        pgnum = (page // limit) + 1
        pages = (count // limit) + 1
        return dict(discussion=self.discussion,
                    posts=q, page=page, limit=limit,
                    status=status, flag=flag,
                    pgnum=pgnum, pages=pages)

    @h.vardec
    @expose()
    def moderate(self, post=None,
                 approve=None,
                 spam=None,
                 delete=None,
                 **kw):
        for args in post:
            if not args.get('checked', False): continue
            post = model.Post.query.get(slug=args['slug'])
            if approve:
                if post.status != 'ok':
                    post.approve()
            elif spam:
                if post.status != 'spam': post.spam()
            elif delete:
                post.delete()
        redirect(request.referer)

class PostRestController(PostController):

    @expose('json:')
    def index(self, **kw):
        return dict(post=self.post)

    @h.vardec
    @expose()
    @validate(pass_validator, error_handler=h.json_validation_error)
    def reply(self, **kw):
        require(has_artifact_access('post', self.thread))
        kw = self.W.edit_post.validate(kw, None)
        post = self.thread.post(parent_id=self.post._id, **kw)
        self.thread.num_replies += 1
        redirect(post.slug.split('/')[-1] + '/')

class ThreadRestController(ThreadController):

    @expose('json:')
    def index(self, **kw):
        return dict(thread=self.thread)

    @h.vardec
    @expose()
    @validate(pass_validator, error_handler=h.json_validation_error)
    def new(self, **kw):
        require(has_artifact_access('post', self.thread))
        kw = self.W.edit_post.validate(kw, None)
        p = self.thread.add_post(**kw)
        redirect(p.slug + '/')

class AppDiscussionRestController(AppDiscussionController):
    ThreadController = ThreadRestController
    PostController = PostRestController

    @expose('json:')
    def index(self, **kw):
        return dict(discussion=self.discussion)

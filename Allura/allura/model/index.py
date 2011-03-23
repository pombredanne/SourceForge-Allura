import re
import logging
from itertools import groupby
from cPickle import dumps, loads
from datetime import datetime
from collections import defaultdict

import bson
import pymongo
from pylons import c, g

import ming
from ming import schema as S
from ming.utils import LazyProperty
from ming.orm import MappedClass, session
from ming.orm import FieldProperty, ForeignIdProperty, RelationProperty

from allura.lib import helpers as h

from .session import main_orm_session

log = logging.getLogger(__name__)

class ArtifactReference(MappedClass):
    '''ArtifactReference manages the artifact graph.

    fields are all strs, corresponding to Solr index_ids
    '''
    class __mongometa__:
        session = main_orm_session
        name = 'artifact_reference'
        indexes = [ 'references' ]

    _id = FieldProperty(str)
    artifact_reference = FieldProperty(S.Object(dict(
            cls=S.Binary,
            project_id=S.ObjectId,
            app_config_id=S.ObjectId,
            artifact_id=S.Anything(if_missing=None))))
    references = FieldProperty([str])

    @classmethod
    def from_artifact(cls, artifact):
        '''Upsert logic to generate an ArtifactReference object from an artifact'''
        obj = cls.query.get(_id=artifact.index_id())
        if obj is not None: return obj
        try:
            obj = cls(
                _id=artifact.index_id(),
                artifact_reference=dict(
                    cls=bson.Binary(dumps(artifact.__class__)),
                    project_id=artifact.app_config.project_id,
                    app_config_id=artifact.app_config._id,
                    artifact_id=artifact._id))
            session(obj).flush(obj)
            return obj
        except pymongo.errors.DuplicateKeyError: # pragma no cover
            session(obj).expunge(obj)
            return cls.query.get(_id=artifact.index_id())

    @LazyProperty
    def artifact(self):
        '''Look up the artifact referenced'''
        aref = self.artifact_reference
        try:
            cls = loads(str(aref.cls))
            with h.push_context(aref.project_id):
                return cls.query.get(_id=aref.artifact_id)
        except:
            log.exception('Error loading artifact for %s: %r',
                          self._id, aref)

class Shortlink(MappedClass):
    '''Collection mapping shorthand_ids for artifacts to ArtifactReferences'''
    class __mongometa__:
        session = main_orm_session
        name = 'shortlink'
        indexes = [
            ('link', 'project_id', 'app_config_id'),
            ('ref_id',),
            ]

    # Stored properties
    _id = FieldProperty(S.ObjectId)
    ref_id = ForeignIdProperty(ArtifactReference)
    project_id = ForeignIdProperty('Project')
    app_config_id = ForeignIdProperty('AppConfig')
    link = FieldProperty(str)
    url = FieldProperty(str)

    # Relation Properties
    project = RelationProperty('Project')
    app_config = RelationProperty('AppConfig')
    ref = RelationProperty('ArtifactReference')

    # Regexes used to find shortlinks
    _core_re = r'''(\[
            (?:(?P<project_id>.*?):)?      # optional project ID
            (?:(?P<app_id>.*?):)?      # optional tool ID
            (?P<artifact_id>.*)             # artifact ID
    \])'''
    re_link_1 = re.compile(r'\s' + _core_re, re.VERBOSE)
    re_link_2 = re.compile(r'^' +  _core_re, re.VERBOSE)

    def __repr__(self):
        with h.push_context(self.project_id):
            return '[%s:%s:%s] -> %s' % (
                self.project.shortname,
                self.app_config.options.mount_point,
                self.link,
                self.ref_id)

    @classmethod
    def lookup(cls, link):
        return cls.from_links(link)[link]

    @classmethod
    def from_artifact(cls, a):
        result = cls.query.get(ref_id=a.index_id())
        if result is None:
            try:
                result = cls(
                    ref_id = a.index_id(),
                    project_id = a.app_config.project_id,
                    app_config_id = a.app_config._id)
                session(result).flush(result)
            except pymongo.errors.DuplicateKeyError: # pragma no cover
                session(result).expunge(result)
                result = cls.query.get(ref_id=a.index_id())
        result.link = a.shorthand_id()
        result.url = a.url()
        return result

    @classmethod
    def from_links(cls, *links):
        '''Convert a sequence of shortlinks to the matching Shortlink objects'''
        # Parse all the links
        parsed_links = dict((link, cls._parse_link(link)) for link in links)
        links_by_artifact = defaultdict(list)
        for link, d in parsed_links.iteritems():
            links_by_artifact[d['artifact']].append(d)

        q = cls.query.find(dict(
                link={'$in': links_by_artifact.keys()}))
        q = q.sort('link')
        result = {}
        matches_by_artifact = dict(
            (link, list(matches))
            for link, matches in groupby(q, key=lambda s:s.link))
        result = {}
        for link, d in parsed_links.iteritems():
            matches = matches_by_artifact.get(d['artifact'], [])
            matches = (
                m for m in matches
                if m.project.shortname == d['project'] )
            if d['app']:
                matches = (
                    m for m in matches
                    if m.app_config.options.mount_point == d['app'])
            matches = list(matches)
            if matches:
                result[link] = matches[0]
            else:
                result[link] = None
            if len(matches) > 1:
                log.warn('Ambiguous link to %s', link)
                for m in matches:
                    log.warn('... %r', m)
        return result

    @classmethod
    def _parse_link(cls, s):
        '''Parse a shortlink into its project/app/artifact parts'''
        s = s.strip()
        if s.startswith('['):
            s = s[1:]
        if s.endswith(']'):
            s = s[:-1]
        parts = s.split(':')
        p_shortname = None
        if hasattr(c, 'project'):
            p_shortname = getattr(c.project, 'shortname', None)
        if len(parts) == 3:
            return dict(
                project=parts[0],
                app=parts[1],
                artifact=parts[2])
        elif len(parts) == 2:
            return dict(
                project=p_shortname,
                app=parts[0],
                artifact=parts[1])
        elif len(parts) == 1:
            return dict(
                project=p_shortname,
                app=None,
                artifact=parts[0])
        else:
            return None

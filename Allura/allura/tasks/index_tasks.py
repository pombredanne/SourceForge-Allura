import sys
import logging
from contextlib import contextmanager

from pylons import g, c

from allura.lib.decorators import task
from allura.lib.exceptions import CompoundError

log = logging.getLogger(__name__)

@task
def add_artifacts(ref_ids):
    '''Add the referenced artifacts to SOLR and shortlinks'''
    from allura import model as M
    from allura.lib.search import find_shortlinks, solarize
    exceptions = []
    with _indexing_disabled(M.session.artifact_orm_session._get()):
        for ref_id in ref_ids:
            try:
                ref = M.ArtifactReference.query.get(_id=ref_id)
                artifact = ref.artifact
                s = solarize(artifact)
                if s is not None:
                    g.solr.add([s])
                if not isinstance(artifact, M.Snapshot):
                    ref.references = [
                        link.ref_id for link in find_shortlinks(s['text']) ]
            except Exception:
                exceptions.append(sys.exc_info())
    if exceptions:
        raise CompoundError(*exceptions)

@task
def del_artifacts(ref_ids):
    from allura import model as M
    for ref_id in ref_ids:
        g.solr.delete(id=ref_id)
    M.ArtifactReference.query.remove(dict(_id={'$in':ref_ids}))
    M.Shortlink.query.remove(dict(ref_id={'$in':ref_ids}))

@task
def commit():
    g.solr.commit()

@contextmanager
def _indexing_disabled(session):
    session.disable_artifact_index = session.skip_mod_date = True
    try:
        yield session
    finally:
        session.disable_artifact_index = session.skip_mod_date = False

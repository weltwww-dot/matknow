import pytest

from matknow.conversations import Conversations,scope_key
from matknow.knowledge import KnowledgeBase,utc_now
from matknow.models import Answer


def saved(kb,identifier):
    answer=Answer(identifier,'问题 '+identifier,'答复','chat',utc_now());kb.save_answer(answer);return answer


def test_refresh_race_claims_only_a_legacy_singleton_without_splitting_conversation(tmp_path):
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');repo=Conversations(kb);scope=scope_key()
    first=saved(kb,'first');identifier=repo.append(first,None,scope)
    second=saved(kb,'second');repo.sync_legacy()  # Another window starts before grouping finishes.
    assert repo.append(second,identifier,scope)==identifier
    assert repo.append(second,identifier,scope)==identifier
    assert len(repo.list())==1 and [a.id for a in repo.load(identifier)[0]]==['first','second']


def test_legacy_migration_is_idempotent_and_large_conversation_is_not_cut_at_50(tmp_path):
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');repo=Conversations(kb);first=saved(kb,'first')
    repo.sync_legacy();repo.sync_legacy();assert len(repo.list())==1
    identifier=repo.append(first,None,scope_key())
    for i in range(60):repo.append(saved(kb,str(i)),identifier,scope_key())
    turns,scopes=Conversations(KnowledgeBase(kb.path)).load(identifier)
    assert len(turns)==61 and len(scopes)==61 and turns[0].id=='first' and turns[-1].id=='59'


def test_cannot_steal_an_answer_from_another_real_conversation_or_direction(tmp_path):
    kb=KnowledgeBase(tmp_path/'a.sqlite3');repo=Conversations(kb);first=saved(kb,'first');second=saved(kb,'second')
    one=repo.append(first,None,scope_key());two=repo.append(second,None,scope_key())
    with pytest.raises(ValueError):repo.append(second,one,scope_key())
    with pytest.raises(ValueError):Conversations(KnowledgeBase(tmp_path/'b.sqlite3')).load(one)
    assert repo.containing(second.id)==two

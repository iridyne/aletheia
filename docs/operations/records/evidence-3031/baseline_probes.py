"""Bounded audit probes, recording current behavior rather than selecting new semantics."""
import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace


def test_current_margin_contract_mismatch():
    from aletheia.risk.engine import _check_liquidation_buffer
    from aletheia.storage.models import TradeProposal
    from aletheia.uta.models import SubmitOrderCommand
    from aletheia.uta.compat import instrument_from_local
    from extensions.exchanges.bitget.profile import _BitgetDemoProfile
    from tests.uta.test_execution_coordinator import _identity
    p = TradeProposal(symbol='BTC/USDT', market_type='usdt_futures', direction='long',
                      price=100, quantity=1, leverage=5, execution_mode='bitget_demo')
    cfg = SimpleNamespace(risk=SimpleNamespace(min_liquidation_buffer_pct=5))
    verdict = _check_liquidation_buffer(p, cfg)
    command = SubmitOrderCommand(command_id='audit', expected_account=_identity('bitget_demo'),
        instrument=instrument_from_local('BTC/USDT','usdt_futures').model_copy(update={'contract_size':Decimal('1')}),
        purpose='entry', proposal_id='audit',client_order_id='audit',side='buy',quantity=Decimal('1'),
        order_type='limit',limit_price=Decimal('100'),leverage=Decimal('5'))
    profile = object.__new__(_BitgetDemoProfile)
    profile._uta = False
    request = profile.submit_request(command)
    assert verdict == [] and request[-1]['marginMode'] == 'cross'
    print('MARGIN',json.dumps({'check18_violations':verdict,'serialized_margin':request[-1]['marginMode'],
        'claim':'check18 alone accepts isolated estimate; not whole Risk approval or an actual liquidation'}))


def test_current_universe_position_attribution(e2e_db,monkeypatch):
    from tests.e2e.test_pipeline_exchange_entry_to_position import _wire_venue, _approving_risk, _private_entry_fill
    from tests.test_entry_plan_execution import _persist_waiting_plan, _execution_receipt_kwargs
    from aletheia.execution.entry_plan_execution import execute_entry_plan
    from aletheia.execution.exchange_materialization import materialize_bitget_entry
    from aletheia.storage import repositories as repo
    from aletheia.analytics.outcome_integrity import attribution_chain_complete
    monkeypatch.setattr('aletheia.execution.entry_plan_execution._require_ai_entry_authority',lambda p:None)
    _approving_risk(monkeypatch)
    submitted,_ = _wire_venue(monkeypatch)
    plan=_persist_waiting_plan(mode='RESTING_MAKER',tactic='POST_ONLY_LIMIT')
    now=datetime.now(timezone.utc)
    result=execute_entry_plan(plan.plan_id,bid=100,ask=100.05,observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),now=now,**_execution_receipt_kwargs(now,depth=False))
    assert result['status']=='submitted' and len(submitted)==1
    order=repo.get_order(result['order_id'])
    fill=_private_entry_fill(order)
    materialized=materialize_bitget_entry(order['id'],[fill])
    assert materialized.state=='filled'
    position=repo.get_position(materialized.position_id)
    proposal=repo.get_trade_proposal(order['proposal_id'])
    assert proposal['entry_plan_id']==plan.plan_id
    assert repo.get_candidate_planning_generation(plan.planning_generation_id)['candidate_id']==plan.candidate_id
    assert position['opportunity_id'] is None
    assert not attribution_chain_complete(position)
    print('ATTRIBUTION',json.dumps({'source_kind':plan.source_kind,'proposal_has_plan':True,
       'position_has_order_and_proposal':bool(position['order_id'] and position['proposal_id']),
       'position_opportunity_id':position['opportunity_id'],'attribution_chain_complete':False}))


def test_current_prompt_hash_conventions():
    from aletheia.trading.entry_contract import CANONICAL_ENTRY_SYSTEM_PROMPT,entry_prompt_fingerprint
    from aletheia.trading.entry_analysis import EntryHypothesis
    from aletheia.model_provider.inference_contract import build_inference_call
    call=build_inference_call(caller='agent_shell.entry_planner',system=CANONICAL_ENTRY_SYSTEM_PROMPT,
        messages=[],context_adapter_id='audit',schema=EntryHypothesis)
    assert call.prompt_static_sha256 != entry_prompt_fingerprint()
    print('PROMPT',json.dumps({'entry':entry_prompt_fingerprint(),'inference':call.prompt_static_sha256}))


def test_prompt_mismatch_reaches_entry_learning(e2e_db,monkeypatch):
    from tests import test_entry_learning_context as fixture
    from aletheia.storage import repositories as repo
    from aletheia.trading.entry_contract import CANONICAL_ENTRY_SYSTEM_PROMPT,entry_prompt_fingerprint
    from aletheia.trading.entry_analysis import EntryHypothesis
    from aletheia.model_provider.inference_contract import build_inference_call
    import hashlib
    execute=repo.execute_write
    def bind_real_prompt(sql,parameters=()):
        if "SET producer_commit='commit-a'" in sql:
            payload=json.loads(parameters[0])
            payload['inference_identity']['prompt_fingerprint']=entry_prompt_fingerprint()
            encoded=json.dumps(payload,sort_keys=True,separators=(',',':'))
            parameters=(encoded,hashlib.sha256(encoded.encode()).hexdigest(),parameters[2])
        return execute(sql,parameters)
    monkeypatch.setattr(repo,'execute_write',bind_real_prompt)
    generation=fixture._generation(created_at='2026-09-01T00:00:00+00:00')
    repo.execute_write("UPDATE candidate_planning_generations SET status='no_plan',result_kind='no_plan',completed_at='2026-09-01T00:01:00+00:00' WHERE id=?",(generation['id'],))
    call=build_inference_call(caller='agent_shell.entry_planner',system=CANONICAL_ENTRY_SYSTEM_PROMPT,
        messages=[],context_adapter_id='audit',schema=EntryHypothesis)
    original=fixture.build_entry_learning_context
    def with_real_prompt(**kwargs):
        kwargs['current_behavior_identity']['prompt_fingerprint']=entry_prompt_fingerprint()
        return original(**kwargs)
    monkeypatch.setattr(fixture,'build_entry_learning_context',with_real_prompt)
    sql='UPDATE llm_inference_calls SET prompt_static_sha256=? WHERE logical_call_id=?'
    repo.execute_write(sql,(entry_prompt_fingerprint(),'inference-'+generation['id']))
    assert fixture._context('2026-09-03T00:00:00+00:00').status=='observational'
    repo.execute_write(sql,(call.prompt_static_sha256,'inference-'+generation['id']))
    context=fixture._context('2026-09-03T00:00:00+00:00')
    assert 'entry_learning_mixed_model_identity' in str(context.model_dump())
    print('PROMPT_INTEGRATION',json.dumps({'before':'observational','after':context.status,
        'reason':'entry_learning_mixed_model_identity'}))

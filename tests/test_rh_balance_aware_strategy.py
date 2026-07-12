#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / 'scripts' / 'rh_balance_aware_strategy.py'
spec = importlib.util.spec_from_file_location('strategy', MOD)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_promotion_fails_on_current_like_metrics():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td); shadow = td/'shadow.json'; arche = td/'arche.json'
        shadow.write_text(json.dumps({'closed_count':20,'profit_factor':1.016,'top_winner_contribution':.734,'statistical_validation':{'bootstrap':{'probability_expectancy_positive':.4534},'monte_carlo':{'max_drawdown_entry_units_p95':3.63}}}))
        arche.write_text(json.dumps({'archetypes':{'MEAN_REVERSION_REBOUND':{'closed':15,'profit_factor':1.37,'top_winner_contribution':.738}}}))
        old_s,old_a=m.SHADOW,m.ARCHETYPES;m.SHADOW,m.ARCHETYPES=shadow,arche
        try:
            cfg={'promotion':{'closed_trades_min':30,'positive_expectancy_probability_min':.75,'profit_factor_min':1.3,'p95_drawdown_entry_units_max':3,'top_winner_contribution_max':.5,'archetype_closed_min':20},'capital_policy':{'entry_eth':.003}}
            result=m.promotion(cfg,{'deployable_after_reserve_eth':.019})
            assert not result['ready']
            assert not result['checks']['profit_factor']
        finally:m.SHADOW,m.ARCHETYPES=old_s,old_a


def test_promotion_passes_only_when_every_check_passes():
    with tempfile.TemporaryDirectory() as td:
        td=Path(td);shadow=td/'shadow.json';arche=td/'arche.json'
        shadow.write_text(json.dumps({'closed_count':35,'profit_factor':1.4,'top_winner_contribution':.4,'statistical_validation':{'bootstrap':{'probability_expectancy_positive':.8},'monte_carlo':{'max_drawdown_entry_units_p95':2.5}}}))
        arche.write_text(json.dumps({'archetypes':{'MEAN_REVERSION_REBOUND':{'closed':25,'profit_factor':1.4,'top_winner_contribution':.4}}}))
        old_s,old_a=m.SHADOW,m.ARCHETYPES;m.SHADOW,m.ARCHETYPES=shadow,arche
        try:
            cfg={'promotion':{'closed_trades_min':30,'positive_expectancy_probability_min':.75,'profit_factor_min':1.3,'p95_drawdown_entry_units_max':3,'top_winner_contribution_max':.5,'archetype_closed_min':20},'capital_policy':{'entry_eth':.003}}
            assert m.promotion(cfg,{'deployable_after_reserve_eth':.019})['ready']
        finally:m.SHADOW,m.ARCHETYPES=old_s,old_a


def test_stale_position_is_not_open():
    with tempfile.TemporaryDirectory() as td:
        p=Path(td)/'positions.json';p.write_text(json.dumps({'open':{'a':{'status':'CLOSED_OR_EMPTY'},'b':{'status':'OPEN','balance_raw':'10'}}}))
        old=m.POSITIONS;m.POSITIONS=p
        try:
            result=m.clean_open_positions();assert result['actual_open_count']==1;assert result['stale_rows']==['a']
        finally:m.POSITIONS=old

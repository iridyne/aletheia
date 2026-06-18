"""Check current strategy state"""
import sqlite3
conn = sqlite3.connect('data/app.db')

print('=== All strategies (current) ===')
for row in conn.execute('''
    SELECT id, name, state, created_at, paper_trading_paused, paper_pause_reason, strategy_type
    FROM strategy_records
    ORDER BY created_at DESC
'''):
    sid = (row[0] or '')[:12]
    name = (row[1] or '')[:50]
    stype = row[6] or '-'
    print(f'  {sid} [{row[2]:15s}] {name:50s} type={stype} paused={row[4]} created={row[3][:16]}')

total = conn.execute("SELECT count(*) FROM strategy_records").fetchone()[0]
print(f'\nTotal strategies: {total}')

print('\n=== Strategies created in last 48h ===')
cnt = conn.execute("SELECT count(*) FROM strategy_records WHERE created_at > datetime('now', '-2 days')").fetchone()[0]
print(f'  Count: {cnt}')

print('\n=== Backtests in last 48h ===')
bt = conn.execute("SELECT count(*) FROM backtest_runs WHERE created_at > datetime('now', '-2 days')").fetchone()[0]
print(f'  Count: {bt}')

print('\n=== Backtests detail (all) ===')
for row in conn.execute('''
    SELECT strategy_id, symbol, timeframe, direction, trade_count,
           round(win_rate,1) as wr, round(expectancy,3) as exp,
           round(profit_factor,2) as pf, status, created_at
    FROM backtest_runs ORDER BY created_at DESC LIMIT 20
'''):
    sid = (row[0] or '')[:12]
    print(f'  {sid} {row[1]:10s} {row[2]:4s} {row[3]:5s} trades={row[4]} wr={row[5]}% exp={row[6]} pf={row[7]} [{row[8]}] {row[9][:16]}')

# Check if strategy_designer has been running more recently
print('\n=== strategy_designer runs (last 7 days) ===')
for row in conn.execute('''
    SELECT date(started_at) as day, count(*) as runs,
           sum(CASE WHEN status='completed' THEN 1 ELSE 0 END) as ok
    FROM agent_runs
    WHERE agent_name='strategy_designer' AND started_at > datetime('now', '-7 days')
    GROUP BY date(started_at)
    ORDER BY day
'''):
    print(f'  {row[0]} runs={row[1]} completed={row[2]}')

conn.close()

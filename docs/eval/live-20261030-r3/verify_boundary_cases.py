"""Offline synthetic regression examples for Claude; no model calls."""
from finsight.domain import Evidence
from finsight.reporting.verifier import verify

refs = {
    'E1': Evidence(id='m:synthetic_a@2025-12-31', kind='metric', label='毛利率', value=.9118, unit='ratio'),
    'E2': Evidence(id='m:synthetic_b@2025-12-31', kind='metric', label='毛利率', value=.7754, unit='ratio'),
    'E3': Evidence(id='m:synthetic_c@2025-12-31', kind='metric', label='增长率', value=.123, unit='ratio'),
}
texts = [
    '毛利率91.18%[E1],99.99%[E2]。',
    '毛利率91.18%[E1], 99.99%[E2]。',
    '营业收入下降12.30%[E3]。',
    '营业收入下降+12.30%[E3]。',
]
for text in texts:
    v = verify(text, refs)
    print(text, {'passed': v.passed, 'claims': [(c.text, c.status, c.message) for c in v.claims]})

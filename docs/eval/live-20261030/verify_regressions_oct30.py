"""Offline reproductions; no external requests and no credentials."""
import json
from pathlib import Path
from finsight.analytics.diagnosis import diagnose
from finsight.config import load_settings
from finsight.data.service import load_service
from finsight.domain import Evidence
from finsight.reporting.report import build_context
from finsight.reporting.verifier import verify

base = Path(__file__).resolve().parent
report_path = base / 'report_600519.json'
if not report_path.exists():
    report_path = base / 'live_oct30' / 'report_600519.json'
result = json.loads(report_path.read_text(encoding='utf-8'))
context = build_context(diagnose(load_service(load_settings().snapshot_dir).company('600519')))
text = result['markdown']
source = '毛利率为91.18%[E17]'
assert text.count(source) == 1
for label, candidate in [('original', text),
                         ('incorrect_compact', text.replace(source, '毛利率为99.99%[E17]')),
                         ('incorrect_spaced', text.replace(source, '毛利率为 99.99%[E17]'))]:
    v = verify(candidate, context.refs)
    print(label, {'passed': v.passed, 'total': v.total, 'mismatch': v.mismatch})

refs = {'E1': Evidence(id='m:synthetic@2025-12-31', kind='metric', label='增长率',
                       value=-0.123, unit='ratio', period='2025-12-31')}
for s in ('增长率为 -12.30%[E1]。', '增长率为 +12.30%[E1]。'):
    v = verify(s, refs)
    print(s, {'passed': v.passed, 'claims': [(c.text, c.status, c.message) for c in v.claims]})

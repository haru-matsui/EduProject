import { spawnSync } from 'node:child_process';
import { readFileSync, readdirSync, mkdirSync, writeFileSync, existsSync, unlinkSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const results = path.join(root, 'tests', 'results');
mkdirSync(results, { recursive: true });
const testFiles = readdirSync(path.join(root, 'tests')).filter(name => name.endsWith('.test.js')).sort().map(name => `tests/${name}`);
const started = new Date();
const nodeTests = spawnSync(process.execPath, ['--test', '--test-reporter=tap', ...testFiles], { cwd: root, encoding: 'utf8', timeout: 30000 });
writeFileSync(path.join(results, 'node-test.tap'), (nodeTests.stdout || '') + (nodeTests.stderr || ''));
const number = name => Number((nodeTests.stdout || '').match(new RegExp(`^# ${name} (\\d+)`, 'm'))?.[1] ?? 0);
const syntaxChecks = ['assets/app.js', 'assets/api.js'].map(file => {
  const checked = spawnSync(process.execPath, ['--check', file], { cwd: root, encoding: 'utf8' });
  return { file, passed: checked.status === 0, exit_code: checked.status };
});
const python = path.join(root, '..', 'backend', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
let httpContract = { status: 'not_run', reason: 'backend virtual environment unavailable' };
if (existsSync(python)) {
  const jsonPath = path.join(results, 'http-contract.json');
  if (existsSync(jsonPath)) unlinkSync(jsonPath);
  const checked = spawnSync(python, ['tests/http_contract.py'], { cwd: root, encoding: 'utf8', timeout: 20000, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
  writeFileSync(path.join(results, 'http-contract.log'), (checked.stdout || '') + (checked.stderr || ''));
  httpContract = existsSync(jsonPath) ? { ...JSON.parse(readFileSync(jsonPath, 'utf8')), exit_code: checked.status } : { status: 'failed_to_run', exit_code: checked.status, error_code: checked.error?.code || null };
}
const report = {
  started_at_utc: started.toISOString(),
  completed_at_utc: new Date().toISOString(),
  node_version: process.version,
  node_tests: { total: number('tests'), passed: number('pass'), failed: number('fail'), skipped: number('skipped'), exit_code: nodeTests.status },
  syntax_checks: syntaxChecks,
  http_contract: httpContract,
  bootstrap: { version: '5.3.3', sha256: createHash('sha256').update(readFileSync(path.join(root, 'assets/vendor/bootstrap.min.css'))).digest('hex') },
  verification_boundaries: {
    visual_browser_check: 'not_run',
    real_telegram_login: 'not_run_no_bot_credentials',
    two_real_browser_sessions_with_postgresql: 'not_run',
    simulated_responses: 'only in tests; production has no fallback data or dev login',
  },
};
writeFileSync(path.join(root, 'testresult.json'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
const passed = nodeTests.status === 0 && number('tests') > 0 && syntaxChecks.every(check => check.passed) && httpContract.exit_code === 0;
process.exitCode = passed ? 0 : 1;

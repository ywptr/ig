import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
const source = fs.readFileSync(new URL('../src/lib/jobDuration.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { jobDurationSeconds } = await import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'));

test('absolute job duration stays five seconds in UTC and UTC+8', () => {
    const previous = process.env.TZ;
    try {
        for (const zone of ['UTC', 'Asia/Makassar']) {
            process.env.TZ = zone;
            for (const status of ['completed', 'failed', 'canceled']) {
                assert.equal(jobDurationSeconds({status, started_at:'2026-11-09T00:00:00+00:00', completed_at:'2026-11-09T00:00:05+00:00'}), 5);
            }
            assert.equal(jobDurationSeconds({status:'running', started_at:'2026-11-09T00:00:00+00:00', completed_at:null}, Date.parse('2026-11-09T00:00:05Z')), 5);
            assert.equal(jobDurationSeconds({status:'queued', started_at:null, completed_at:null}), null);
        }
        process.env.TZ = 'Asia/Makassar';
        const now = Date.parse('2026-11-09T00:00:05Z');
        assert.equal((now - Date.parse('2026-11-09T00:00:00')) / 1000, 28805);
        assert.equal(jobDurationSeconds({started_at:'2026-11-09T00:00:00Z', completed_at:null}, now), 5);
    } finally { if(previous===undefined) delete process.env.TZ; else process.env.TZ=previous; }
});

"""Judge completed pairs while the live benchmark continues."""
import asyncio
import json
import os
from pathlib import Path
from openai import AsyncOpenAI
from gaia_agent.providers import OpenAIProvider
import live_judge as judge

judge.INPUT = judge.ROOT / "blind-terra-20261111.jsonl"
judge.OUTPUT = judge.ROOT / "blind-terra-20261111-judgments.jsonl"
judge.INSTRUCTIONS += " The baseline received only the question and had no search or other tools; do not count missing citations as an error. Use the reference only to assess substantive correctness."

async def main():
    cases = {x['id']: x for x in json.loads((judge.ROOT / 'public/cases.json').read_text())}
    refs = {x['id']: x for x in json.loads((judge.ROOT / 'private/reference.json').read_text())}
    if len(cases) != 20 or set(cases) != set(refs):
        raise ValueError('invalid inputs or output already exists')
    done = set()
    if judge.OUTPUT.exists():
        for line in judge.OUTPUT.read_text().splitlines():
            row = json.loads(line)
            if 'judgments' in row:
                done.add(row['case_id'])
    gate = asyncio.Semaphore(2)
    async with AsyncOpenAI(api_key=os.environ['OPENAI_API_KEY'], max_retries=0) as client:
        provider = OpenAIProvider(client, model='gpt-6.1-terra')
        async def run(case, rows):
            async with gate:
                try:
                    async with asyncio.timeout(180):
                        row = await judge.grade(provider, case, refs[case['id']], rows, [])
                except Exception as exc:
                    row = {'case_id': case['id'], 'error': type(exc).__name__}
                with judge.OUTPUT.open('a', encoding='utf-8') as f:
                    f.write(json.dumps(row, ensure_ascii=False) + '\n')
                print(case['id'], 'judged' if 'judgments' in row else row['error'], flush=True)
        while len(done) < len(cases):
            rows = {}
            if judge.INPUT.exists():
                for line in judge.INPUT.read_text().splitlines():
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    rows[(item['case_id'], item['arm'])] = item
            ready = [c for c in cases.values() if c['id'] not in done and all((c['id'], a) in rows for a in ('baseline','toyagent'))]
            if ready:
                done.update(c['id'] for c in ready)
                await asyncio.gather(*(run(c, rows) for c in ready))
            else:
                await asyncio.sleep(10)

if __name__ == '__main__':
    asyncio.run(main())

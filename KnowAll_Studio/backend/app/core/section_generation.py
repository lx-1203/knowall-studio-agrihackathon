"""Bounded source-aware generation for the existing quiz entry point."""
import asyncio
from dataclasses import replace

from app.core.learning_workbench import build_sections, allocate_budget, source_fingerprint, question_quality


async def generate_section_questions(chunks, config, model, generator):
    sections = build_sections(chunks, max_chars=6000)
    if len(sections) > 200:
        from fastapi import HTTPException
        raise HTTPException(413, '资料超过 200 个分段，请拆分资料后生成')
    budgets = allocate_budget([len(s['text']) for s in sections], config.count)
    semaphore = asyncio.Semaphore(2)
    fingerprint = source_fingerprint(chunks)

    async def run(section, count):
        if count == 0:
            return [], None
        try:
            async with semaphore:
                result = await generator.generate(section['text'], replace(config, count=count), model)
            return [{**q, 'source_chunk_id': section['chunk_ids'][0], 'source_section': section['title'],
                     'source_hash': fingerprint} for q in result[:count]], None
        except Exception:
            return [], dict(section_id=section['id'], title=section['title'], message='本段生成失败，请重试')

    outputs = await asyncio.gather(*(run(s, n) for s, n in zip(sections, budgets)))
    questions = [q for items, _ in outputs for q in items]
    errors = [error for _, error in outputs if error]
    coverage = dict(total_sections=len(sections),
                    covered_sections=sum(any(question_quality(q)['eligible'] for q in items) for items, _ in outputs),
                    uncovered_sections=sum(not any(question_quality(q)['eligible'] for q in items) for items, _ in outputs),
                    budget_skipped_sections=sum(n == 0 for n in budgets), failed_sections=len(errors))
    return questions, coverage, errors

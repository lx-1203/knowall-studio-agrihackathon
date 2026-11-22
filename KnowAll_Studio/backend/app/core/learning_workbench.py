"""Pure, deterministic source segmentation and learning interaction rules.

Matching/classification options contain label, text and choices (target strings).
Only correct_answer contains the answer mapping; options never expose mappings.
Ordering answers are JSON arrays containing each option label exactly once.
"""
import hashlib
import json
import math
import re
from fractions import Fraction


def _get(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def _ordered(chunks):
    return sorted(list(chunks), key=lambda c: _get(c, 'chunk_index', 0) or 0)


def source_fingerprint(chunks):
    data = [[str(_get(c, 'id', '')), _get(c, 'text_content', '') or '',
             str(_get(c, 'page_range', '') or '')] for c in _ordered(chunks)]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def build_sections(chunks, max_chars=6000):
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
        raise ValueError('max_chars must be a positive integer')
    chunks = _ordered(chunks)
    fingerprint = source_fingerprint(chunks)
    sections = []
    title = 'Section'
    for chunk in chunks:
        text = _get(chunk, 'text_content', '') or ''
        if not text.strip():
            continue
        boundaries = [0] + [m.start() for m in re.finditer(r'(?m)^#{1,6}\s+\S', text) if m.start()] + [len(text)]
        for start, end in zip(boundaries, boundaries[1:]):
            block = text[start:end]
            heading = re.match(r'^#{1,6}\s+([^\n]+)', block)
            if heading:
                title = heading.group(1).strip().rstrip('#').strip()
            for offset in range(0, len(block), max_chars):
                piece = block[offset:offset + max_chars]
                if not piece.strip():
                    continue
                # A new heading starts a chapter even when its title repeats.
                same_title = sections and (sections[-1]['title'] == title or (title == 'Section' and sections[-1]['title'].startswith('Section ')))
                if same_title and not (heading and offset == 0) and len(sections[-1]['text']) + len(piece) <= max_chars:
                    section = sections[-1]
                    section['text'] += piece
                    for key, value in [('chunk_ids', str(_get(chunk, 'id', ''))),
                                       ('page_ranges', str(_get(chunk, 'page_range', '') or ''))]:
                        if value not in section[key]:
                            section[key].append(value)
                    continue
                index = len(sections)
                sections.append(dict(id=hashlib.sha256(f'{fingerprint}:{index}'.encode()).hexdigest()[:24],
                                     title=title if title != 'Section' else f'Section {index + 1}',
                                     text=piece, chunk_ids=[str(_get(chunk, 'id', ''))],
                                     page_ranges=[str(_get(chunk, 'page_range', '') or '')]))
    return sections


def allocate_budget(weights, total):
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError('total must be a nonnegative integer')
    if any(isinstance(w, bool) or not isinstance(w, (int, float)) or not math.isfinite(w) or w < 0 for w in weights):
        raise ValueError('weights must be finite nonnegative numbers')
    positive = [i for i, w in enumerate(weights) if w > 0]
    result = [0] * len(weights)
    if not positive:
        if total:
            raise ValueError('positive budget needs a positive weight')
        return result
    if total < len(positive):
        for i in sorted(positive, key=lambda i: (-weights[i], i))[:total]:
            result[i] = 1
        return result
    for i in positive:
        result[i] = 1
    remaining = total - len(positive)
    exact = [Fraction(str(w)) for w in weights]
    denominator = sum(exact)
    quotas = [remaining * w / denominator for w in exact]
    floors = [q.numerator // q.denominator for q in quotas]
    result = [a + b for a, b in zip(result, floors)]
    for i in sorted(positive, key=lambda i: (-(quotas[i] - floors[i]), i))[:total - sum(result)]:
        result[i] += 1
    return result


def _kind(q):
    return q.get('question_type') or q.get('type') or 'short_answer'


def _answer(q):
    return q.get('correct_answer') if q.get('correct_answer') is not None else q.get('answer')


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


def _labels(value, labels):
    if isinstance(value, str) and value.strip().startswith('['):
        # Legacy question banks stored Python list repr; new providers use JSON.
        # literal_eval only parses data, and containers other than string lists fail closed.
        import ast
        try:
            value = ast.literal_eval(value.strip())
        except (ValueError, SyntaxError, RecursionError):
            return []
    if isinstance(value, list):
        return [x.strip().upper() for x in value] if all(isinstance(x, str) and x.strip() for x in value) else []
    text = str(value or '').strip().upper()
    if text in labels:
        return [text]
    parts = re.split(r'[\s,，;；]+', text)
    if len(parts) > 1:
        return [p for p in parts if p]
    return list(text) if labels and all(len(x) == 1 for x in labels) else [text]


def question_quality(question):
    from pydantic import ValidationError
    from app.core.quiz.metadata import question_rubrics
    issues = []
    try:
        question_rubrics(question)
    except ValidationError:
        issues.append('invalid_rubric')
    if not str(question.get('question_text') or question.get('question') or question.get('prompt') or '').strip():
        issues.append('empty_prompt')
    answer = _answer(question)
    if answer is None or not str(answer).strip():
        issues.append('empty_answer')
    kind = _kind(question)
    if kind not in {'single_choice', 'multi_choice', 'true_false', 'short_answer', 'fill_blank', 'calculation', 'essay', 'matching', 'classification', 'ordering', 'cloze', 'formula', 'coding', 'material_analysis', 'term_definition'}:
        issues.append('unsupported_type')
    choice = kind in {'single_choice', 'multi_choice', 'matching', 'classification', 'ordering'}
    options = question.get('options') or []
    if isinstance(options, str):
        options = _json(options)
    labels = []
    texts = []
    if choice or options:
        if not isinstance(options, list) or len(options) < 2:
            issues.append('insufficient_options')
            options = []
        for option in options:
            if not isinstance(option, dict):
                issues.append('malformed_option')
                continue
            label = str(option.get('label') or '').strip().upper()
            text = str(option.get('text') or '').strip()
            if not label or not text or label in labels or text.casefold() in texts:
                issues.append('empty_or_duplicate_option')
            labels.append(label)
            texts.append(text.casefold())
    if kind in {'single_choice', 'multi_choice'}:
        selected = _labels(answer, labels)
        if not selected or len(set(selected)) != len(selected) or any(x not in labels for x in selected):
            issues.append('unknown_answer_label')
        if kind == 'single_choice' and len(selected) != 1:
            issues.append('single_choice_needs_one_answer')
    elif kind in {'matching', 'classification'}:
        mapping = _json(answer)
        if not isinstance(mapping, dict) or set(mapping) != set(labels):
            issues.append('invalid_answer_mapping')
        elif not issues:
            for option in options:
                candidates = option.get('choices')
                if (not isinstance(candidates, list) or len(candidates) < 2
                        or any(not isinstance(x, str) or not x.strip() for x in candidates)
                        or len(set(candidates)) != len(candidates)
                        or option['label'] not in mapping
                        or mapping.get(option['label']) not in candidates):
                    issues.append('invalid_target_choices')
    elif kind == 'ordering':
        order = _json(answer)
        if not isinstance(order, list) or any(not isinstance(x, str) for x in order) or len(order) != len(labels) or set(order) != set(labels):
            issues.append('invalid_order_permutation')
    elif kind == 'true_false' and _boolean_answer(answer, options) is None:
        issues.append('invalid_boolean_answer')
    return dict(eligible=not issues, issues=issues)


def _boolean(value):
    text = str(value).strip().casefold()
    if text in {'true', '1', 'yes', '正确', '对', '是', 't'}:
        return True
    if text in {'false', '0', 'no', '错误', '错', '否', 'f'}:
        return False
    return None


def _boolean_answer(value, options):
    """Resolve explicit option meaning before boolean aliases (A need not mean true)."""
    text = str(value).strip().casefold()
    for option in options:
        if isinstance(option, dict) and str(option.get('label', '')).strip().casefold() == text:
            return _boolean(option.get('text'))
    return _boolean(value)


def grade_interaction(question, user_answer):
    correct = _answer(question)
    result = dict(is_correct=False, score_ratio=0.0, correct_answer=correct, analysis=question.get('analysis') or '')
    if not question_quality(question)['eligible']:
        return result
    kind = _kind(question)
    ratio = 0.0
    if kind in {'matching', 'classification'}:
        expected, actual = _json(correct), _json(user_answer)
        if isinstance(actual, dict) and set(actual).issubset(expected):
            ratio = sum(actual.get(k) == v for k, v in expected.items()) / len(expected)
    elif kind == 'ordering':
        expected, actual = _json(correct), _json(user_answer)
        if isinstance(actual, list) and all(isinstance(x, str) for x in actual) and len(actual) == len(expected) and set(actual) == set(expected):
            ratio = sum(a == b for a, b in zip(expected, actual)) / len(expected)
    elif kind in {'single_choice', 'multi_choice'}:
        options = question.get('options')
        options = _json(options) if isinstance(options, str) else options
        labels = [str(o['label']).strip().upper() for o in options]
        actual = _labels(user_answer, labels)
        ratio = float(len(actual) == len(set(actual)) and set(actual) == set(_labels(correct, labels)))
    elif kind == 'true_false':
        options = question.get('options') or []
        options = _json(options) if isinstance(options, str) else options
        actual = _boolean_answer(user_answer, options)
        ratio = float(actual is not None and actual == _boolean_answer(correct, options))
    elif (kind == 'fill_blank' and question.get('blanks')) or (kind in {'short_answer', 'term_definition'} and question.get('scoring_points')):
        from app.core.quiz import exam_engine
        ratio = exam_engine._score_open_answer(user_answer, question, kind)['score_ratio']
    else:
        normalized = lambda x: re.sub(r'\s+', '', str(x or '')).casefold()
        ratio = float(bool(normalized(user_answer)) and normalized(user_answer) == normalized(correct))
        keywords = question.get('keywords')
        if not ratio and isinstance(keywords, list) and keywords and all(isinstance(x, str) and x.strip() for x in keywords):
            ratio = float(sum(normalized(k) in normalized(user_answer) for k in keywords) / len(keywords) >= .5)
    result.update(is_correct=ratio == 1, score_ratio=ratio)
    return result

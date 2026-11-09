"""OpenAI-compatible AI service for classroom interactive answering."""

import json
import re
from typing import Any, Dict, List, Optional

import httpx

from .providers.base import ProviderError
from .providers.chaoxing import text

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


def get_ai_config(store) -> Dict[str, str]:
    config = store.get("config", "ai_api", {}) or {}
    return {
        "base_url": config.get("base_url") or DEFAULT_BASE_URL,
        "model": config.get("model") or DEFAULT_MODEL,
        "api_key": config.get("api_key") or "",
    }


def normalize_base_url(url: str) -> str:
    cleaned = (url or "").strip().rstrip("/")
    if cleaned.endswith("/chat/completions"):
        cleaned = cleaned[:-len("/chat/completions")].rstrip("/")
    return cleaned or DEFAULT_BASE_URL


def save_ai_config(store, base_url: str, model: str, api_key: str):
    cleaned_url = normalize_base_url(base_url)
    cleaned_model = (model or "").strip() or DEFAULT_MODEL
    cleaned_key = (api_key or "").strip()
    store.put(
        "config",
        "ai_api",
        {
            "base_url": cleaned_url,
            "model": cleaned_model,
            "api_key": cleaned_key,
        },
    )


async def test_ai_connection(config: Dict[str, str]) -> str:
    api_key = (config.get("api_key") or "").strip()
    if not api_key:
        raise ProviderError("请先填写 API KEY")
    base_url = normalize_base_url(config.get("base_url") or DEFAULT_BASE_URL)
    model = (config.get("model") or "").strip() or DEFAULT_MODEL
    url = f"{base_url}/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "请回复四个字：连接成功"}],
                    "max_tokens": 50,
                    "temperature": 0.1,
                },
            )
            if resp.status_code != 200:
                detail = resp.text
                try:
                    err_json = resp.json()
                    detail = err_json.get("error", {}).get("message") or str(err_json)
                except Exception:
                    pass
                raise ProviderError(f"API 请求失败 (HTTP {resp.status_code}): {detail}")
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return f"测试成功！模型回复：{content.strip()}"
    except httpx.RequestError as exc:
        raise ProviderError(f"无法连接到 API 地址 ({url})：{exc}") from exc


def format_questions_for_ai(questions: List[Dict[str, Any]], course_name: str = "", active_title: str = "") -> List[Dict[str, Any]]:
    formatted = []
    for idx, q in enumerate(questions):
        qid = str(q.get("id", ""))
        typ = int(q.get("type", 0))
        stem = text(q.get("content") or q.get("title") or q.get("name") or "")
        opts = q.get("answer") or q.get("options") or []

        type_map = {
            0: "单选题",
            1: "多选题",
            2: "填空题",
            3: "单选题",
            9: "填空题",
            10: "填空题",
            16: "判断题",
            4: "简答题",
            43: "单选投票",
        }
        type_name = type_map.get(typ, "简答题")
        item: Dict[str, Any] = {
            "index": idx + 1,
            "qid": qid,
            "type_code": typ,
            "type_name": type_name,
            "stem": stem,
        }

        if typ in (0, 1, 3, 16):
            formatted_opts = []
            for o in opts:
                name = str(o.get("name") or o.get("key", ""))
                cnt = text(o.get("content") or o.get("value", ""))
                formatted_opts.append(f"{name}: {cnt}")
            item["options"] = formatted_opts
        elif typ in (2, 9, 10):
            item["blank_count"] = max(1, len(opts))

        formatted.append(item)
    return formatted


def extract_json(raw: str) -> Optional[Dict[str, Any]]:
    raw = raw.strip()
    # Try finding markdown code fence ```json ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass
    # Try finding first { and last }
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except Exception:
            pass
    return None


def parse_ai_answers(response_text: str, questions: List[Dict[str, Any]]) -> Dict[str, Any]:
    parsed_json = extract_json(response_text) or {}
    raw_answers = parsed_json.get("answers") or parsed_json

    result: Dict[str, Any] = {}
    for idx, q in enumerate(questions):
        qid = str(q.get("id", ""))
        typ = int(q.get("type", 0))
        opts = q.get("answer") or q.get("options") or []
        val = raw_answers.get(qid) if isinstance(raw_answers, dict) else None

        # Fallback to key by 1-based index if qid is missing
        if val is None and isinstance(raw_answers, dict):
            val = raw_answers.get(str(idx + 1)) or raw_answers.get(idx + 1)

        opt_names = [str(o.get("name") or o.get("key", "")).strip() for o in opts if (o.get("name") or o.get("key"))]

        if typ in (0, 3):
            # Single choice
            chosen = str(val or "").strip().upper()
            match = re.search(r"[A-Z]", chosen)
            if match and match.group(0) in opt_names:
                result[qid] = match.group(0)
            elif opt_names and chosen in opt_names:
                result[qid] = chosen
            else:
                result[qid] = opt_names[0] if opt_names else "A"

        elif typ == 1:
            # Multiple choice
            if isinstance(val, list):
                chosen_list = [str(item).strip().upper() for item in val]
            else:
                chosen_list = list(re.findall(r"[A-Z]", str(val or "").upper()))
            valid = [k for k in opt_names if k in chosen_list]
            if not valid:
                valid = [chosen_list[0]] if chosen_list else (opt_names[:1] if opt_names else ["A"])
            result[qid] = valid

        elif typ == 16:
            # Judge / True-False (options usually "1": 对, "2": 错 or "A": 正确, "B": 错误)
            raw_str = str(val or "").strip()
            # Map Chinese / True / False to options
            true_keys = [o.get("name") for o in opts if any(w in str(o.get("content", "")) for w in ("对", "正确", "√", "T", "true"))]
            false_keys = [o.get("name") for o in opts if any(w in str(o.get("content", "")) for w in ("错", "错误", "×", "F", "false"))]
            true_key = str(true_keys[0]) if true_keys else (opt_names[0] if opt_names else "1")
            false_key = str(false_keys[0]) if false_keys else (opt_names[1] if len(opt_names) > 1 else "2")

            if any(w in raw_str for w in ("对", "正确", "true", "True", "TRUE", "T", "t", "√")):
                result[qid] = true_key
            elif any(w in raw_str for w in ("错", "错误", "false", "False", "FALSE", "F", "f", "×")):
                result[qid] = false_key
            elif raw_str in opt_names:
                result[qid] = raw_str
            else:
                result[qid] = true_key

        elif typ in (2, 9, 10):
            # Fill in the blanks
            blank_count = max(1, len(opts))
            if isinstance(val, list):
                blanks = [str(v).strip() for v in val]
            elif isinstance(val, str):
                parts = [p.strip() for p in re.split(r"[;；,，\n]+", val) if p.strip()]
                blanks = parts if parts else [val.strip()]
            else:
                blanks = []

            # Clean and ensure non-empty
            cleaned_blanks = []
            for b in blanks:
                cleaned_blanks.append(b if b else "略")

            while len(cleaned_blanks) < blank_count:
                cleaned_blanks.append("略")
            result[qid] = cleaned_blanks[:blank_count]

        else:
            # Short answer / essay
            ans_str = str(val or "").strip()
            if not ans_str or ans_str.lower() in ("null", "none"):
                ans_str = "根据微观经济学原理进行分析与解答。"
            result[qid] = ans_str

    return result


async def solve_questions(
    config: Dict[str, str],
    questions: List[Dict[str, Any]],
    course_name: str = "",
    active_name: str = "",
) -> Dict[str, Any]:
    api_key = (config.get("api_key") or "").strip()
    if not api_key:
        raise ProviderError("未配置 AI API KEY，请在系统设置中配置后再试")
    base_url = normalize_base_url(config.get("base_url") or DEFAULT_BASE_URL)
    model = (config.get("model") or "").strip() or DEFAULT_MODEL
    url = f"{base_url}/chat/completions"

    formatted_q = format_questions_for_ai(questions, course_name, active_name)
    system_prompt = (
        "你是一名大学优秀学生助手，正在作答课堂互动练习题。"
        f"{'当前课程为：' + course_name if course_name else ''}\n"
        "请仔细审题并给出正确、精确的答案。\n"
        "【严格要求】：必须仅输出一个 JSON 对象，格式如下：\n"
        "{\n"
        '  "answers": {\n'
        '    "<题目qid>": <答案值>\n'
        "  }\n"
        "}\n"
        "具体题型的答案值格式规范：\n"
        "1. 单选题：单个选项字母，如 \"A\" 或 \"C\"\n"
        "2. 多选题：选项字母列表，如 [\"A\", \"B\", \"C\"]\n"
        "3. 判断题：选项对应标识，通常为 \"1\"（对）或 \"2\"（错），或选项名 \"A\"/\"B\"\n"
        "4. 填空题：按空位顺序排列的字符串数组，如 [\"4\", \"9\", \"5\"]，长度需与题目空位数一致\n"
        "5. 简答题：简练切题的文字解答（字符串）\n"
        "不要包含任何多余的解释、前缀或代码块外的废话。"
    )

    user_content = json.dumps(
        {
            "activity": active_name,
            "questions": formatted_q,
        },
        ensure_ascii=False,
        indent=2,
    )

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    "temperature": 0.2,
                },
            )
        except httpx.RequestError as exc:
            raise ProviderError(f"请求 AI 服务失败 ({url})：{exc}") from exc

        if resp.status_code != 200:
            detail = resp.text
            try:
                detail = resp.json().get("error", {}).get("message") or detail
            except Exception:
                pass
            raise ProviderError(f"AI 服务响应错误 (HTTP {resp.status_code}): {detail}")

        data = resp.json()
        raw_reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not raw_reply:
            raise ProviderError("AI 未返回有效内容")

        return parse_ai_answers(raw_reply, questions)

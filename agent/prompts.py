"""System prompt, language detection and the injection check. One prompt for both providers."""

from __future__ import annotations

import re

from mcp_server.scope import Scope

BRANCH_NAMES = {"B1": "B1 Olaya / العليا (Riyadh, SAR)", "B2": "B2 Tahlia / التحلية (Jeddah, SAR)",
                "B3": "B3 Smouha / سموحة (Alexandria, EGP)",
                "B4": "B4 Al Malqa / الملقا (Riyadh, SAR, opened 2026-04-01)"}
POLICY_FACTS = """Approval facts (for explaining who approves a request; for any other policy question use search_sop):
- Reorders: area managers approve up to SAR 5,000 (EGP 65,000) in their area; the owner approves above that,
  every Jeddah (B2) and Alexandria (B3) reorder, and any reorder an area manager requested.
- Price changes: requested by the owner or an area manager with a written reason; only the owner approves;
  they take effect the next business day.
- Waste target: at most 3% of stock used per month; above 5% in any week needs a written explanation."""
ROLE_NAMES = {"owner": "owner", "area_manager": "area manager", "branch_manager": "branch manager",
              "staff": "staff member"}

ARABIC = re.compile(r"[؀-ۿ]")
LATIN = re.compile(r"[A-Za-z]")

INJECTION = re.compile(
    r"ignore (all |any |your |the |previous |prior )*(rules|instructions|prompt|restrictions)"
    r"|disregard (your|the|all) (rules|instructions)|you are now|act as (the )?(owner|admin)"
    r"|system prompt|developer mode|jailbreak|bypass (the )?(rules|restrictions|permissions)"
    r"|انس[ىي] (كل )?(التعليمات|القواعد)|تجاهل (كل )?(التعليمات|القواعد)|اعتبرني (المالك|المدير)"
    r"|بدون قيود|اكسر القواعد",
    re.IGNORECASE)


def detect_language(text: str) -> str:
    return "ar" if len(ARABIC.findall(text)) >= len(LATIN.findall(text)) and ARABIC.search(text) else "en"


def looks_like_injection(text: str) -> bool:
    return bool(INJECTION.search(text))


REFUSALS = {
    "en": "I can't change my rules or your access. I can help with the branches and data your role "
          "covers. What would you like to know?",
    "ar": "لا أستطيع تغيير قواعدي أو صلاحياتك. أقدر أساعدك في الفروع والبيانات المسموحة لدورك. وش تحب تعرف؟",
}
UNVERIFIED_NOTE = {
    "en": "\n\n_Note: I couldn't confirm these figures against the data: {nums}. Treat them with care._",
    "ar": "\n\n_ملاحظة: لم أتمكن من مطابقة هذه الأرقام مع البيانات: {nums}. تعامل معها بحذر._",
}
TOO_MANY_STEPS = {
    "en": "I needed too many lookups for that. Could you ask about one branch or one period at a time?",
    "ar": "احتجت خطوات كثيرة للإجابة. ممكن تسأل عن فرع واحد أو فترة واحدة في كل مرة؟",
}
NO_MODEL = {
    "en": "The assistant is unavailable right now. Please try again in a minute.",
    "ar": "المساعد غير متاح الآن. حاول مرة ثانية بعد دقيقة.",
}


def system_prompt(scope: Scope, user_name: str, language: str) -> str:
    branches = ", ".join(BRANCH_NAMES[b] for b in scope.branches)
    lang = "Arabic (Gulf register, clear and polite)" if language == "ar" else "English"
    return f"""You are the operations assistant for Sufra Café & Bakery (سُفرة), a 4-branch chain.

User: {user_name}, {ROLE_NAMES[scope.role]}. Branches they can see: {branches}.
Data covers 2026-01-01 to 2026-06-30. "This month" or "last month" mean the latest data month
(June 2026) or the one before, unless the user says otherwise; state the dates you used.

Rules:
1. Get every number from a tool. Never estimate, round up or invent a figure, and never do arithmetic
   the tools didn't do unless you show it. If no tool returns it, say you don't have it.
2. Reply in {lang}, the user's language. Use Western digits with comma thousands separators
   (195,013) and the currency (SAR or EGP). Use the branch names above, e.g. التحلية for B2.
   Keep answers short: the answer first, then 1–3 supporting facts.
3. Say which period and branches the numbers cover, and repeat any notes the tool returned
   (e.g. a branch open for only part of the period, dates without data).
4. If a tool returns `not_permitted`, tell the user in one sentence that their role doesn't have
   access, without guessing or hinting at the data. Don't retry with other branches.
5. Reorders and price changes: when the user asks for one, call request_reorder or propose_price_change
   straight away with what they said (branch id like B1; litres → l, kilos → kg). Don't ask them to confirm
   in text: the app shows them a Confirm / Cancel step before anything is created. Ask only if something
   required is missing. You never approve a request: say who must approve it and that nothing changes
   until they do.
6. Any question about a policy, procedure, limit or rule (discounts, complaints, food safety, temperatures,
   checklists, opening times, scheduling, staffing, waste, reorders, price changes): call search_sop first,
   then answer from what it returns and name the document. Don't answer these from memory.
7. Only help with Sufra operations. Politely decline anything else.

{POLICY_FACTS}"""

// food-lookup 순수 로직 — 요청 검증 · 엔진 경로 매핑 (외부 의존 0)
// 설계 IP/integration/meal_food_edit_design_v1.md §3-3 · 평가 F01~F06 (lookup_core.test.ts)

export type LookupRequest =
  | { action: "search"; q: string; limit: number }
  | { action: "resolve"; name: string; serving_g: number | null; portion_choice?: PortionChoice; rice_separated?: boolean };

// 분량 조절 v1 (2026-10-13) — 엔진 portion.PORTION_FACTOR 의 키. 배율은 엔진에만 있다(여기선 이름만 검사).
export const PORTION_CHOICES = ["적게", "보통", "많게"] as const;
export type PortionChoice = typeof PORTION_CHOICES[number];

export type Parsed = { ok: true; req: LookupRequest } | { ok: false; message: string };

export const Q_MAX = 30;
export const NAME_MAX = 40;
export const SERVING_MAX = 2000;
export const LIMIT_DEFAULT = 10;
export const LIMIT_MAX = 20;

export function parseLookupRequest(body: unknown): Parsed {
  if (!body || typeof body !== "object" || Array.isArray(body)) return { ok: false, message: "body must be object" };
  const b = body as Record<string, unknown>;
  if (b.action === "search") {
    if (typeof b.q !== "string") return { ok: false, message: "q must be string" };
    const q = b.q.trim();
    if (q.length < 1 || q.length > Q_MAX) return { ok: false, message: `q length 1..${Q_MAX}` };
    let limit = LIMIT_DEFAULT;
    if (b.limit !== undefined) {
      if (typeof b.limit !== "number" || !Number.isInteger(b.limit) || b.limit < 1 || b.limit > LIMIT_MAX) {
        return { ok: false, message: `limit 1..${LIMIT_MAX}` };
      }
      limit = b.limit;
    }
    return { ok: true, req: { action: "search", q, limit } };
  }
  if (b.action === "resolve") {
    if (typeof b.name !== "string") return { ok: false, message: "name must be string" };
    const name = b.name.trim();
    if (name.length < 1 || name.length > NAME_MAX) return { ok: false, message: `name length 1..${NAME_MAX}` };
    const s = b.serving_g;
    let serving: number | null = null;
    if (s !== undefined && s !== null) {
      if (typeof s !== "number" || !Number.isFinite(s) || s <= 0 || s > SERVING_MAX) {
        return { ok: false, message: `serving_g null or (0, ${SERVING_MAX}]` };
      }
      serving = s;
    }
    const req: Extract<LookupRequest, { action: "resolve" }> = { action: "resolve", name, serving_g: serving };
    if (b.portion_choice !== undefined) {
      if (!(PORTION_CHOICES as readonly unknown[]).includes(b.portion_choice)) {
        return { ok: false, message: `portion_choice ${PORTION_CHOICES.join("|")}` };
      }
      if (serving === null) return { ok: false, message: "portion_choice needs serving_g (base)" };
      req.portion_choice = b.portion_choice as PortionChoice;
    }
    if (b.rice_separated !== undefined) {
      if (typeof b.rice_separated !== "boolean") return { ok: false, message: "rice_separated must be boolean" };
      req.rice_separated = b.rice_separated;
    }
    return { ok: true, req };
  }
  return { ok: false, message: "action must be search|resolve" };
}

/** 엔진 경로 + 본문 (엔진 tools/test_server.py _handle_v1_food_lookup) */
export function engineCall(req: LookupRequest): { path: string; body: Record<string, unknown> } {
  return req.action === "search"
    ? { path: "/v1/food/search", body: { q: req.q, limit: req.limit } }
    : {
        path: "/v1/food/resolve",
        body: {
          name: req.name, serving_g: req.serving_g,
          ...(req.portion_choice !== undefined ? { portion_choice: req.portion_choice } : {}),
          ...(req.rice_separated !== undefined ? { rice_separated: req.rice_separated } : {}),
        },
      };
}

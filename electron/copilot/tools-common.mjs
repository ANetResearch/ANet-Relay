// Shared pieces of the three Copilot tool sets (04 §6.2): result helpers and the claim / skill / plan helpers that
// flight and device tools use. Tools talk to Relay's REST API through a RelayHttp client (agent.mjs).
import { Type } from "@earendil-works/pi-ai";

export { Type };

export const SESSION_NAME = "copilot";
export const SKILL_TIMEOUT_MS = 90_000;
export const PLAN_TIMEOUT_MS = 240_000;

export const VID = Type.String({ description: "Aircraft id from relay_fleet, e.g. sim-1 or real-10-7-7-36" });

export const text = (value) => ({ content: [{ type: "text", text: typeof value === "string" ? value : JSON.stringify(value) }], details: {} });

export const sleep = (ms, signal) =>
  new Promise((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(t);
      reject(new Error("aborted"));
    };
    const t = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });

export const enc = encodeURIComponent;

export function compactVehicle(v) {
  return {
    id: v.id,
    label: v.label,
    kind: v.kind,
    autopilot: v.autopilot,
    link: v.link?.state,
    armed: v.armed,
    landed: v.landed,
    mode: v.mode,
    altitude_m: v.altitude_m,
    heading_deg: v.heading_deg,
    battery: v.battery && { voltage: v.battery.voltage, remaining_pct: v.battery.remaining_pct, level: v.battery.level },
    controller: v.controller,
    claim: v.claim?.name ?? null,
    possessed: Boolean(v.possession),
    capabilities: v.capabilities,
    preflight_ok: v.preflight?.ok,
    skill: v.skill,
    plan: v.plan && { state: v.plan.state, index: v.plan.index, count: v.plan.count, detail: v.plan.detail },
  };
}

/** Claim, skill and plan helpers on a Relay API client. */
export function flightHelpers(relay) {
  const vehicle = (id) => relay.call("GET", `/vehicles/${enc(id)}`);

  /** Claim an aircraft for Copilot unless a person flies it or another session holds it. */
  const claimFor = async (id) => {
    const v = await vehicle(id);
    if (v.possession) {
      const err = new Error(
        `possessed_by_human: the operator is flying ${v.label} (${v.possession.input}). Ask them to release it (P, or hold Options) to hand it to you.`,
      );
      err.code = "possessed_by_human";
      throw err;
    }
    if (v.claim && v.claim.name !== SESSION_NAME) {
      throw new Error(`claimed_by_other: ${v.claim.name} holds ${v.label}. Do not take it over unless the user asks.`);
    }
    if (!v.claim) await relay.call("POST", `/vehicles/${enc(id)}/claim`);
    return v;
  };

  /** Stop button: end what Copilot started. A plan is cancelled; a skill is replaced by hold (disarm on the ground). */
  const halt = async (id, what) => {
    try {
      if (what === "plan") await relay.call("POST", `/vehicles/${enc(id)}/plan/cancel`);
      else await relay.call("POST", `/vehicles/${enc(id)}/skills/hold`, {});
    } catch {
      try {
        const v = await vehicle(id);
        if (v.armed && v.landed === "ground" && !v.possession) await relay.call("POST", `/vehicles/${enc(id)}/arm`, { arm: false });
      } catch {
        // the operator still has possession and stop
      }
    }
  };
  const halting = (id, what, signal) => (err) => {
    if (signal?.aborted) return halt(id, what).then(() => Promise.reject(err));
    return Promise.reject(err);
  };

  const waitSkill = async (id, skillId, signal, timeoutMs = SKILL_TIMEOUT_MS) => {
    const end = Date.now() + timeoutMs;
    while (Date.now() < end) {
      const v = await vehicle(id);
      const s = v.skill;
      if (!s || s.id !== skillId) return { state: "superseded", vehicle: compactVehicle(v) };
      if (s.state !== "running") return { state: s.state, detail: s.detail, vehicle: compactVehicle(v) };
      await sleep(250, signal).catch(halting(id, "skill", signal));
    }
    return { state: "timeout" };
  };

  const skill = async (id, name, params, signal) => {
    await claimFor(id);
    const started = await relay.call("POST", `/vehicles/${enc(id)}/skills/${name}`, params);
    const result = await waitSkill(id, started.skill.id, signal);
    if (result.state === "failed") throw new Error(`${name} failed: ${result.detail}`);
    return result;
  };

  const waitPlan = async (id, planId, signal, onUpdate) => {
    const end = Date.now() + PLAN_TIMEOUT_MS;
    let shown = -1;
    while (Date.now() < end) {
      const v = await vehicle(id);
      const plan = v.plan;
      if (!plan || plan.id !== planId) throw new Error("the sequence was replaced by another command");
      if (plan.index !== shown && plan.state === "running") {
        shown = plan.index;
        onUpdate?.(text(`step ${plan.index + 1} of ${plan.count}: ${plan.steps[plan.index]?.action}`));
      }
      if (plan.state === "done") return { state: "done", results: plan.results, vehicle: compactVehicle(v) };
      if (plan.state !== "running") throw new Error(`sequence ${plan.state} at step ${plan.index + 1}: ${plan.detail}`);
      await sleep(300, signal).catch(halting(id, "plan", signal));
    }
    throw new Error("the sequence did not finish in time");
  };

  return { vehicle, claimFor, waitSkill, skill, waitPlan };
}

/** A tool definition from a compact description; `run(params, ctx)` returns a JSON-able value. */
export function defineTool(name, label, description, parameters, run) {
  return {
    name,
    label,
    description,
    parameters,
    executionMode: "sequential",
    execute: async (_id, params, signal, onUpdate) => text(await run(params ?? {}, { signal, onUpdate })),
  };
}

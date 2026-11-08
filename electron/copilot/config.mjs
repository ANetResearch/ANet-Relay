// Copilot endpoint settings and API key storage (06 §9). Pure Node: the Electron objects (safeStorage, the
// userData path) are injected, so electron/test/secrets.test.mjs runs it under plain `node --test`.
//
// Files in the app data folder:
//   copilot.json          endpoint, model, tool scope, opacity, keyStorage; keyEnc only when the key lives in the
//                         system keychain (safeStorage ciphertext)
//   copilot-secrets.json  the API key, AES-256-GCM, written in the default storage ("file")
//   copilot-secrets.key   the 256-bit key for that file, random per installation
// All three are 0600 on POSIX; on Windows both secret files get an ACL that grants only the current user
// (icacls, no inheritance). safeStorage is touched only when keyStorage is "keychain" (opt-in in Settings),
// never at start-up, when Copilot opens or when its status is read; isEncryptionAvailable() included.
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export const APIS = ["openai-completions", "anthropic-messages", "openai-responses"];
export const TOOL_SCOPES = ["flight", "flight+device", "all"];
export const KEY_STORAGES = ["file", "keychain"];
export const OPACITY_RANGE = [60, 100];
export const DEFAULTS = { api: "openai-completions", baseUrl: "", model: "", toolScope: "all", opacity: 100 };

export const CONFIG_FILE = "copilot.json";
export const SECRETS_FILE = "copilot-secrets.json";
export const SECRETS_KEY_FILE = "copilot-secrets.key";
const AAD = Buffer.from("anet-relay/copilot-secrets/v1");

export function clampOpacity(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return DEFAULTS.opacity;
  return Math.min(OPACITY_RANGE[1], Math.max(OPACITY_RANGE[0], Math.round(n)));
}

function validBaseUrl(url) {
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:";
  } catch {
    return false;
  }
}

/**
 * Windows: replace the inherited ACL with one that grants full control to the current user only. The file
 * already sits in the user's profile (inherits user, SYSTEM, Administrators); this removes the other two.
 * Best effort: a failure leaves the inherited per-profile ACL and is logged, never thrown.
 */
export function restrictAclWindows(file) {
  const user = process.env.USERNAME;
  if (!user) return false;
  const who = process.env.USERDOMAIN ? `${process.env.USERDOMAIN}\\${user}` : user;
  try {
    execFileSync("icacls", [file, "/inheritance:r", "/grant:r", `${who}:F`], { stdio: "ignore", windowsHide: true, timeout: 5000 });
    return true;
  } catch (err) {
    console.error(`[relay] copilot: could not restrict the ACL of ${path.basename(file)}: ${err?.message ?? err}`);
    return false;
  }
}

/**
 * @param {object} o
 * @param {() => string} o.dir the app data folder (Electron userData)
 * @param {() => import("electron").SafeStorage} o.keychain returns Electron's safeStorage; called only when the
 *   keychain option is on
 * @param {string} [o.platform] process.platform
 * @param {(file: string) => unknown} [o.restrictAcl] Windows ACL hook (tests inject a stub)
 */
export function createCopilotConfig({ dir, keychain, platform = process.platform, restrictAcl = restrictAclWindows }) {
  const file = (name) => path.join(dir(), name);

  const protect = (target) => {
    if (platform === "win32") restrictAcl(target);
    else fs.chmodSync(target, 0o600);
  };

  const writePrivate = (target, data) => {
    fs.mkdirSync(dir(), { recursive: true });
    const tmp = `${target}.tmp`;
    fs.writeFileSync(tmp, data, { mode: 0o600 });
    fs.renameSync(tmp, target);
    protect(target);
  };

  const readRaw = () => {
    try {
      const raw = JSON.parse(fs.readFileSync(file(CONFIG_FILE), "utf8"));
      return raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
    } catch {
      return {};
    }
  };

  const writeConfig = (cfg) => writePrivate(file(CONFIG_FILE), JSON.stringify(cfg, null, 2));

  // --- the file store (default) --------------------------------------------------------------------------

  /** The per-installation AES key; created on the first write only (reading never creates files). */
  const fileKey = (create) => {
    try {
      const key = Buffer.from(fs.readFileSync(file(SECRETS_KEY_FILE), "utf8").trim(), "base64");
      if (key.length === 32) return key;
      if (!create) return null;
    } catch {
      if (!create) return null;
    }
    const key = crypto.randomBytes(32);
    writePrivate(file(SECRETS_KEY_FILE), `${key.toString("base64")}\n`);
    return key;
  };

  const readSecrets = () => {
    let blob;
    try {
      blob = JSON.parse(fs.readFileSync(file(SECRETS_FILE), "utf8"));
    } catch {
      return null;
    }
    const key = fileKey(false);
    if (!key || blob?.v !== 1 || blob.alg !== "aes-256-gcm") return null;
    try {
      const decipher = crypto.createDecipheriv("aes-256-gcm", key, Buffer.from(blob.iv, "base64"));
      decipher.setAAD(AAD);
      decipher.setAuthTag(Buffer.from(blob.tag, "base64"));
      const plain = Buffer.concat([decipher.update(Buffer.from(blob.data, "base64")), decipher.final()]).toString("utf8");
      const parsed = JSON.parse(plain);
      return typeof parsed?.apiKey === "string" ? parsed : null;
    } catch {
      return null; // tampered, truncated or a key file from another installation
    }
  };

  /** Fingerprint of the stored file secret (its IV changes on every write); no decryption. */
  const secretsStamp = () => {
    try {
      return JSON.parse(fs.readFileSync(file(SECRETS_FILE), "utf8"))?.iv ?? null;
    } catch {
      return null;
    }
  };

  const writeSecrets = (apiKey) => {
    const key = fileKey(true);
    const iv = crypto.randomBytes(12);
    const cipher = crypto.createCipheriv("aes-256-gcm", key, iv);
    cipher.setAAD(AAD);
    const data = Buffer.concat([cipher.update(JSON.stringify({ apiKey }), "utf8"), cipher.final()]);
    const blob = { v: 1, alg: "aes-256-gcm", iv: iv.toString("base64"), tag: cipher.getAuthTag().toString("base64"), data: data.toString("base64") };
    writePrivate(file(SECRETS_FILE), JSON.stringify(blob));
  };

  const removeSecrets = () => fs.rmSync(file(SECRETS_FILE), { force: true });

  // --- the keychain store (opt-in) -----------------------------------------------------------------------

  const keychainEncrypt = (apiKey) => {
    const ss = keychain();
    if (!ss.isEncryptionAvailable()) throw new Error("The system keychain is not available on this computer.");
    return ss.encryptString(apiKey).toString("base64");
  };

  const keychainDecrypt = (enc) => {
    try {
      return keychain().decryptString(Buffer.from(enc, "base64"));
    } catch {
      return "";
    }
  };

  // --- settings ------------------------------------------------------------------------------------------

  /**
   * Settings as stored, normalized. A pre-0.4 copilot.json has keyEnc and no keyStorage: the key stays in the
   * keychain untouched (never decrypted automatically) and keyStorage reads as "file" with a pending migration.
   * A pre-0.4 keyPlain (0600 plain text, systems without a keychain) moves into the file store on first load.
   */
  const load = () => {
    const raw = readRaw();
    const cfg = { ...DEFAULTS, ...raw };
    if (!KEY_STORAGES.includes(cfg.keyStorage)) cfg.keyStorage = "file";
    if (typeof cfg.keyPlain === "string") {
      const plain = cfg.keyPlain;
      delete cfg.keyPlain;
      if (plain && !readSecrets()) writeSecrets(plain);
      writeConfig(cfg);
    }
    return cfg;
  };

  /** True when a pre-0.4 keychain key waits for the operator's choice (06 §9 migration). */
  const migrationPending = (cfg) => cfg.keyStorage === "file" && typeof cfg.keyEnc === "string" && cfg.keyEnc.length > 0;

  /** The API key for a request (agent build, connection test). Keychain mode decrypts here and only here. */
  const apiKey = (cfg = load()) => {
    if (cfg.keyStorage === "keychain") return cfg.keyEnc ? keychainDecrypt(cfg.keyEnc) : "";
    return readSecrets()?.apiKey ?? "";
  };

  /** What identifies the stored key without reading it: agents are rebuilt when it changes. */
  const keyStamp = (cfg) => (cfg.keyStorage === "keychain" ? `k:${cfg.keyEnc ?? ""}` : `f:${secretsStamp() ?? ""}`);

  /** The renderer's view (CopilotConfig in electron/relay.d.ts). Never decrypts a keychain key. */
  const view = (cfg = load()) => {
    let hasKey;
    let keyHint;
    if (cfg.keyStorage === "keychain") {
      hasKey = Boolean(cfg.keyEnc);
      keyHint = hasKey && typeof cfg.keyHint === "string" ? cfg.keyHint : null;
    } else {
      const key = readSecrets()?.apiKey ?? "";
      hasKey = key.length > 0;
      keyHint = key.length >= 8 ? key.slice(-4) : null;
    }
    return {
      api: cfg.api,
      baseUrl: cfg.baseUrl,
      model: cfg.model,
      hasKey,
      keyHint,
      keyStorage: cfg.keyStorage,
      keyMigration: migrationPending(cfg) ? "keychain" : null,
      configured: Boolean(cfg.baseUrl && cfg.model),
      toolScope: TOOL_SCOPES.includes(cfg.toolScope) ? cfg.toolScope : DEFAULTS.toolScope,
      opacity: clampOpacity(cfg.opacity),
    };
  };

  const clean = (input, current) => {
    const api = APIS.includes(input?.api) ? input.api : current.api;
    const baseUrl = typeof input?.baseUrl === "string" ? input.baseUrl.trim().replace(/\/+$/, "") : current.baseUrl;
    const model = typeof input?.model === "string" ? input.model.trim() : current.model;
    if (baseUrl && !validBaseUrl(baseUrl)) throw new Error("The endpoint must be an http(s) URL.");
    if (baseUrl.length > 512 || model.length > 200) throw new Error("The endpoint or model name is too long.");
    const toolScope = TOOL_SCOPES.includes(input?.toolScope) ? input.toolScope : TOOL_SCOPES.includes(current.toolScope) ? current.toolScope : DEFAULTS.toolScope;
    const opacity = input?.opacity === undefined ? clampOpacity(current.opacity) : clampOpacity(input.opacity);
    return { api, baseUrl, model, toolScope, opacity };
  };

  /**
   * Apply a CopilotConfigInput. apiKey: a string stores it in the active storage, null or "" clears every stored
   * key, absent keeps it. keyStorage switches the storage and moves a stored key across (the operator's own
   * action, so the keychain may be asked; a key that cannot be moved is cleared and must be entered again).
   * Entering a key while a migration is pending stores it in the file store and drops the keychain copy
   * without decrypting it.
   * @returns {{ before: object, cfg: object }}
   */
  const save = (input) => {
    const before = load();
    const next = { ...before, ...clean(input, before) };
    const target = KEY_STORAGES.includes(input?.keyStorage) ? input.keyStorage : before.keyStorage;
    const hasNewKey = input && Object.hasOwn(input, "apiKey");
    const newKey = hasNewKey && typeof input.apiKey === "string" ? input.apiKey.trim() : "";
    if (newKey.length > 4096) throw new Error("The API key is too long.");

    // Everything that can fail (the keychain refusing, above all) runs before anything is written, so a refused
    // save leaves the stored key and copilot.json as they were. secrets: what then happens to the file store.
    let secrets = null; // null: unchanged; { remove: true }; { write: key }
    if (target !== before.keyStorage) {
      if (target === "keychain") {
        // Opt in. A pre-0.4 keyEnc is kept as is ("Keep using keychain"); a file key moves into the keychain.
        if (!hasNewKey) {
          const fileSecret = readSecrets()?.apiKey ?? "";
          if (fileSecret) {
            next.keyEnc = keychainEncrypt(fileSecret);
            next.keyHint = fileSecret.length >= 8 ? fileSecret.slice(-4) : null;
          } else if (!next.keyEnc && !keychain().isEncryptionAvailable()) {
            throw new Error("The system keychain is not available on this computer.");
          }
        }
        secrets = { remove: true };
      } else {
        // Opt out: the keychain key moves into the file store (one keychain read, asked for by the operator).
        if (!hasNewKey && next.keyEnc) {
          const moved = keychainDecrypt(next.keyEnc);
          if (moved) secrets = { write: moved };
        }
        delete next.keyEnc;
        delete next.keyHint;
      }
      next.keyStorage = target;
    }

    if (hasNewKey) {
      const enc = newKey && next.keyStorage === "keychain" ? keychainEncrypt(newKey) : null;
      delete next.keyEnc;
      delete next.keyHint;
      if (enc) {
        next.keyEnc = enc;
        next.keyHint = newKey.length >= 8 ? newKey.slice(-4) : null;
        secrets = { remove: true };
      } else {
        secrets = newKey ? { write: newKey } : { remove: true };
      }
    }
    if (secrets?.write) writeSecrets(secrets.write);
    else if (secrets?.remove) removeSecrets();
    writeConfig(next);
    return { before, cfg: next };
  };

  /** Floating window opacity, read when the window is created at start-up. */
  const opacity = () => clampOpacity(load().opacity);

  return { load, view, save, apiKey, keyStamp, opacity, migrationPending, paths: { config: () => file(CONFIG_FILE), secrets: () => file(SECRETS_FILE), key: () => file(SECRETS_KEY_FILE) } };
}

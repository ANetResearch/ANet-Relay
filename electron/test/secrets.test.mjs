// Copilot key storage (06 §9): the default file store never touches safeStorage, the keychain is opt-in, and a
// pre-0.4 keychain key is never decrypted automatically.
//   node --test electron/test/secrets.test.mjs
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, test } from "node:test";

import { createCopilotConfig, SECRETS_FILE, SECRETS_KEY_FILE } from "../copilot/config.mjs";

const KEY = "sk-test-0123456789abcdef";
const dirs = [];

afterEach(() => {
  for (const d of dirs.splice(0)) fs.rmSync(d, { recursive: true, force: true });
});

/** A safeStorage stand-in that records every property access (a call needs one) and every call. */
function keychainStub({ available = true } = {}) {
  const calls = [];
  const impl = {
    isEncryptionAvailable: () => available,
    encryptString: (s) => Buffer.from(`enc:${s}`, "utf8"),
    decryptString: (b) => {
      const s = Buffer.from(b).toString("utf8");
      if (!s.startsWith("enc:")) throw new Error("bad ciphertext");
      return s.slice(4);
    },
  };
  const proxy = new Proxy(impl, {
    get(target, prop) {
      calls.push(String(prop));
      return target[prop];
    },
  });
  let handed = 0;
  return { calls, handed: () => handed, keychain: () => (handed++, proxy) };
}

function setup(opts = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "relay-secrets-"));
  dirs.push(dir);
  const stub = keychainStub(opts);
  const acl = [];
  const store = createCopilotConfig({ dir: () => dir, keychain: stub.keychain, platform: opts.platform, restrictAcl: (f) => acl.push(path.basename(f)) });
  return { dir, stub, store, acl };
}

const read = (dir, name) => fs.readFileSync(path.join(dir, name), "utf8");
const writeLegacy = (dir, cfg) => fs.writeFileSync(path.join(dir, "copilot.json"), JSON.stringify(cfg));

test("default configuration: zero safeStorage calls across start-up, status, open, save and load", () => {
  const { dir, stub, store } = setup();
  // start-up: the floating window reads its opacity before anything else
  assert.equal(store.opacity(), 100);
  // status / open: the main window and the Copilot window read the view (also on every focus)
  assert.equal(store.view().hasKey, false);
  assert.equal(store.view().keyStorage, "file");
  // save an endpoint, then a key, then change the opacity
  store.save({ api: "openai-completions", baseUrl: "https://api.example.com/v1/", model: "m-1" });
  store.save({ apiKey: KEY });
  store.save({ opacity: 80 });
  // load: what the agent build and the connection test use
  assert.equal(store.apiKey(), KEY);
  const v = store.view();
  assert.deepEqual(
    { hasKey: v.hasKey, keyHint: v.keyHint, keyStorage: v.keyStorage, keyMigration: v.keyMigration, configured: v.configured, baseUrl: v.baseUrl, opacity: v.opacity },
    { hasKey: true, keyHint: "cdef", keyStorage: "file", keyMigration: null, configured: true, baseUrl: "https://api.example.com/v1", opacity: 80 },
  );
  assert.ok(store.keyStamp(store.load()).startsWith("f:"));
  // a second "process" over the same folder
  const again = createCopilotConfig({ dir: () => dir, keychain: stub.keychain });
  assert.equal(again.opacity(), 80);
  assert.equal(again.apiKey(), KEY);
  again.save({ apiKey: null });
  assert.equal(again.view().hasKey, false);

  assert.equal(stub.handed(), 0, "safeStorage must not even be fetched");
  assert.deepEqual(stub.calls, []);
});

test("the file store is AES-256-GCM, private, and holds no plain text", () => {
  const { dir, store } = setup();
  store.save({ baseUrl: "https://api.example.com", model: "m", apiKey: KEY });
  for (const name of ["copilot.json", SECRETS_FILE, SECRETS_KEY_FILE]) {
    assert.ok(!read(dir, name).includes(KEY), `${name} must not contain the key`);
    if (process.platform !== "win32") assert.equal(fs.statSync(path.join(dir, name)).mode & 0o777, 0o600, name);
  }
  const blob = JSON.parse(read(dir, SECRETS_FILE));
  assert.equal(blob.alg, "aes-256-gcm");
  assert.equal(Buffer.from(blob.iv, "base64").length, 12);
  assert.equal(Buffer.from(read(dir, SECRETS_KEY_FILE).trim(), "base64").length, 32);
  // a new IV on every write: the stamp changes, so an agent built on the old key is rebuilt
  const s1 = store.keyStamp(store.load());
  store.save({ apiKey: `${KEY}-2` });
  assert.notEqual(store.keyStamp(store.load()), s1);
  // reading never creates files
  const empty = setup();
  empty.store.view();
  empty.store.apiKey();
  assert.deepEqual(fs.readdirSync(empty.dir), []);
});

test("a tampered secret or a foreign key file reads as no key", () => {
  const { dir, store } = setup();
  store.save({ apiKey: KEY });
  const blob = JSON.parse(read(dir, SECRETS_FILE));
  const data = Buffer.from(blob.data, "base64");
  data[0] ^= 1;
  fs.writeFileSync(path.join(dir, SECRETS_FILE), JSON.stringify({ ...blob, data: data.toString("base64") }));
  assert.equal(store.apiKey(), "");
  assert.equal(store.view().hasKey, false);
  store.save({ apiKey: KEY });
  fs.writeFileSync(path.join(dir, SECRETS_KEY_FILE), Buffer.alloc(32, 7).toString("base64"));
  assert.equal(store.apiKey(), "");
});

test("Windows: both secret files get the current-user ACL instead of chmod", () => {
  const { acl, store } = setup({ platform: "win32" });
  store.save({ apiKey: KEY });
  assert.ok(acl.includes(SECRETS_FILE));
  assert.ok(acl.includes(SECRETS_KEY_FILE));
  assert.ok(acl.includes("copilot.json"));
});

test("a pre-0.4 keyPlain moves into the file store without the keychain", () => {
  const { dir, stub, store } = setup();
  writeLegacy(dir, { baseUrl: "https://a.example", model: "m", keyPlain: KEY });
  assert.equal(store.view().hasKey, true);
  assert.equal(store.apiKey(), KEY);
  assert.ok(!read(dir, "copilot.json").includes(KEY));
  assert.deepEqual(stub.calls, []);
});

test("migration: a pre-0.4 keychain key is never decrypted automatically", () => {
  const { dir, stub, store } = setup();
  writeLegacy(dir, { baseUrl: "https://a.example", model: "m", keyEnc: Buffer.from(`enc:${KEY}`).toString("base64") });
  const v = store.view();
  assert.equal(v.keyMigration, "keychain");
  assert.equal(v.keyStorage, "file");
  assert.equal(v.hasKey, false);
  assert.equal(store.apiKey(), "");
  assert.equal(store.opacity(), 100);
  store.save({ opacity: 70 }); // unrelated saves keep the keychain copy for a later choice
  assert.equal(store.view().keyMigration, "keychain");
  assert.equal(stub.handed(), 0);

  // "Enter key again": the new key goes to the file store and the keychain copy is dropped unread
  store.save({ apiKey: "sk-new-key-99998888" });
  assert.equal(store.view().keyMigration, null);
  assert.equal(store.apiKey(), "sk-new-key-99998888");
  assert.ok(!("keyEnc" in JSON.parse(read(dir, "copilot.json"))));
  assert.equal(stub.handed(), 0);
});

test("migration: Keep using keychain turns the option on and only then reads the keychain", () => {
  const { dir, stub, store } = setup();
  writeLegacy(dir, { baseUrl: "https://a.example", model: "m", keyEnc: Buffer.from(`enc:${KEY}`).toString("base64") });
  store.save({ keyStorage: "keychain" });
  const v = store.view();
  assert.deepEqual([v.keyStorage, v.keyMigration, v.hasKey, v.keyHint], ["keychain", null, true, null]);
  assert.deepEqual(stub.calls, [], "turning the option on with a stored keychain key needs no call");
  assert.equal(store.apiKey(), KEY);
  assert.deepEqual(stub.calls, ["decryptString"]);
  assert.ok(store.keyStamp(store.load()).startsWith("k:"));
});

test("keychain opt-in moves a file key in, opt-out moves it back", () => {
  const { dir, stub, store } = setup();
  store.save({ apiKey: KEY });
  store.save({ keyStorage: "keychain" });
  assert.deepEqual(stub.calls, ["isEncryptionAvailable", "encryptString"]);
  assert.equal(fs.existsSync(path.join(dir, SECRETS_FILE)), false);
  assert.deepEqual([store.view().hasKey, store.view().keyHint], [true, "cdef"]);
  assert.equal(stub.calls.length, 2, "the view does not decrypt");
  store.save({ apiKey: "sk-another-key-7777" });
  assert.equal(store.apiKey(), "sk-another-key-7777");
  store.save({ keyStorage: "file" });
  const v = store.view();
  assert.deepEqual([v.keyStorage, v.hasKey, v.keyHint], ["file", true, "7777"]);
  assert.ok(!("keyEnc" in JSON.parse(read(dir, "copilot.json"))));
  const n = stub.calls.length;
  store.apiKey();
  store.view();
  assert.equal(stub.calls.length, n, "back in file mode: no further calls");
});

test("keychain opt-in refuses when the system offers none and keeps the file key", () => {
  const { store } = setup({ available: false });
  store.save({ apiKey: KEY });
  assert.throws(() => store.save({ keyStorage: "keychain" }), /not available/);
  assert.deepEqual([store.view().keyStorage, store.apiKey()], ["file", KEY]);
});

test("validation is unchanged", () => {
  const { store } = setup();
  assert.throws(() => store.save({ baseUrl: "ftp://x" }), /http/);
  assert.throws(() => store.save({ apiKey: "x".repeat(4097) }), /too long/);
  assert.equal(store.save({ opacity: 10 }).cfg.opacity, 60);
  assert.equal(store.save({ toolScope: "nope" }).cfg.toolScope, "all");
});

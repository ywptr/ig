import test from "node:test";
import assert from "node:assert/strict";
import { sourceURL } from "./import-source.mjs";
const m = await import(sourceURL("lib/mentions.ts"));
const r = await import(sourceURL("lib/references.ts"));
const { buildMediaRequest, INITIAL_FORM } = await import(sourceURL("lib/generation.ts"));
const slot = { name: "refs", media_types: ["image"], mime_types: ["image/png"], roles: ["character", "style"], min_count: 0, max_count: 2, max_bytes: 100, strengths: ["normal", "strong"] };
const video = { ...slot, name: "first_frame", roles: ["first_frame"], max_count: 1, strengths: [] };
const artifact = (id = "private-uuid", filename = "hero.png", extra = {}) => ({ artifact_id: id, filename, media_type: "image", mime_type: "image/png", size_bytes: 10, ...extra });
const draft = (prompt = "Use @hero", references = []) => ({ prompt, references, mentions: [] });
function insert(value = draft(), item = artifact(), role = "character", slots = [slot], id = "mention-1") {
    const trigger = m.activeMention(value.prompt, value.prompt.length, value.mentions);
    assert.ok(trigger); return m.insertMention(value, trigger, item, slots, role, id);
}
test("@ triggers at a word boundary and captures only the active query", () => {
    for (const text of ["@", "@cat", "Use @reference"]) assert.equal(m.activeMention(text, text.length, []).query, text.split("@").at(-1));
    for (const text of ["email@example.com", "@cat ", "plain"]) assert.equal(m.activeMention(text, text.length, []), null);
});
test("raw @filename stays ordinary text and never creates attachment authority", () => {
    const value = draft("Use @hero.png"); assert.equal(m.compilePrompt(value, [slot]), value.prompt);
    assert.deepEqual(r.structuredReferences(value.references), []);
});
test("explicit selection creates an ID-backed span and existing structured attachment", () => {
    const value = insert(); const mention = value.mentions[0];
    assert.equal(mention.mention_id, "mention-1"); assert.equal(mention.artifact_id, "private-uuid");
    assert.equal(value.prompt.slice(mention.start, mention.end), "@hero.png");
    assert.equal(m.mentionDetails(mention, value.references).role, "character");
    assert.equal(value.references[0].source, "mention");
    assert.deepEqual(r.structuredReferences(value.references), [{ artifact_id: "private-uuid", role: "character", strength: "normal" }]);
});
test("multiple roles require explicit selection; no arbitrary role is inferred", () => {
    assert.deepEqual(m.mentionChoices(artifact(), [], [slot]).map(choice => choice.role), ["character", "style"]);
    assert.throws(() => insert(draft(), artifact(), "", [slot]));
    assert.equal(insert(draft(), artifact(), "style").references[0].role, "style");
});
test("repeat mention selection reuses one reference and the chosen role", () => {
    let value = insert(); value = m.editPrompt(value, value.prompt + "and @hero");
    value = insert(value, artifact(), "character", [slot], "mention-2");
    assert.equal(value.mentions.length, 2); assert.equal(value.references.length, 1);
    assert.equal(m.compilePrompt(value, [slot]), "Use reference image 1 and reference image 1");
    assert.throws(() => insert(m.editPrompt(value, value.prompt + "@hero"), artifact(), "style"), /eligible/);
});
test("mention of an independently attached artifact reuses its role and survives mention deletion", () => {
    const references = r.attachArtifact(artifact(), [], slot, "style");
    const value = insert(draft("Use @hero", references), artifact(), "style");
    assert.equal(value.references.length, 1); assert.equal(value.references[0].source, undefined);
    assert.equal(m.editPrompt(value, "Plain prompt", 12).references.length, 1);
});
test("last mention deletion removes only mention-created attachment", () => {
    const value = insert();
    const cleared = m.editPrompt(value, "Use ", 4);
    assert.equal(cleared.mentions.length, 0); assert.equal(cleared.references.length, 0);
});
test("removing one repeated token keeps the attachment and disambiguates identical labels using caret", () => {
    let value = insert(draft("@hero")); value = insert(m.editPrompt(value, value.prompt + "@hero"), artifact(), "character", [slot], "mention-2");
    const first = value.mentions[0];
    value = m.editPrompt(value, value.prompt.slice(first.end + 1), 0);
    assert.equal(value.mentions.length, 1); assert.equal(value.mentions[0].mention_id, "mention-2"); assert.equal(value.references.length, 1);
});
test("identical filenames from different artifacts keep distinct IDs through deletion", () => {
    let value = insert(draft("@hero"), artifact("a"));
    value = insert(m.editPrompt(value, value.prompt + "@hero"), artifact("b"), "character", [slot], "mention-b");
    value = m.editPrompt(value, value.prompt.slice(value.mentions[0].end + 1), 0);
    assert.equal(value.references[0].artifact_id, "b"); assert.equal(value.mentions[0].artifact_id, "b");
});
test("editing before/after a token shifts spans without changing identity; editing inside makes plain text", () => {
    let value = insert(); const id = value.mentions[0].mention_id;
    value = m.editPrompt(value, "Now " + value.prompt, 4);
    assert.equal(value.mentions[0].start, 8); assert.equal(value.mentions[0].mention_id, id);
    value = m.editPrompt(value, value.prompt + "beside a tower", value.prompt.length + 14);
    assert.equal(value.mentions[0].mention_id, id);
    const start = value.mentions[0].start;
    value = m.editPrompt(value, value.prompt.slice(0, start + 2) + "x" + value.prompt.slice(start + 2), start + 3);
    assert.equal(value.mentions.length, 0); assert.equal(value.references.length, 0);
});
test("card removal converts linked token to ordinary text without guessing another attachment", () => {
    const value = insert(); const next = m.updateAttachments(value, []);
    assert.equal(next.prompt, value.prompt); assert.equal(next.mentions.length, 0);
    assert.equal(m.compilePrompt(next, [slot]), value.prompt.trim());
});
test("role and strength changes synchronize every mention through the single attachment", () => {
    const value = insert(); value.references[0] = { ...value.references[0], role: "style", strength: "strong" };
    const details = m.mentionDetails(value.mentions[0], value.references);
    assert.equal(details.role, "style"); assert.equal(details.strength, "strong");
    assert.deepEqual(r.structuredReferences(value.references), [{ artifact_id: "private-uuid", role: "style", strength: "strong" }]);
});
test("filename rename and search reordering cannot change a selected ID or label snapshot", () => {
    const value = insert(); value.references[0].artifact = artifact("private-uuid", "renamed.png");
    assert.equal(value.mentions[0].display_label, "hero.png");
    assert.equal(m.compilePrompt(value, [slot]), "Use reference image 1");
});
test("incompatible media, MIME, sizes, roles and full inputs cannot create mentions", () => {
    for (const extra of [{ media_type: "video" }, { mime_type: "image/gif" }, { size_bytes: 101 }])
        assert.equal(m.mentionChoices(artifact("bad", "bad", extra), [], [slot]).length, 0);
    const full = r.attachArtifact(artifact("a"), [], video, "first_frame");
    assert.equal(m.mentionChoices(artifact("b"), full, [video]).length, 0);
    assert.equal(m.mentionChoices(artifact("a"), full, [video]).length, 1);
});
test("video’s only first_frame role resolves without strength; compile uses plain reference wording", () => {
    const value = insert(draft(), artifact(), "first_frame", [video]);
    assert.equal(m.mentionChoices(artifact(), [], [video]).length, 1);
    assert.deepEqual(r.structuredReferences(value.references), [{ artifact_id: "private-uuid", role: "first_frame" }]);
    assert.equal(m.compilePrompt(value, [video]), "Use first frame (reference image 1)");
});
test("model changes preserve draft but invalidate incompatible mentioned references", () => {
    const value = insert(); assert.match(m.mentionErrors(value, [video])[0], /incompatible/);
    assert.throws(() => m.compilePrompt(value, [video]), /incompatible/); assert.equal(value.prompt, "Use @hero.png ");
});
test("broken, overlapping or mismatched span state blocks compilation", () => {
    const value = insert();
    assert.throws(() => m.compilePrompt({ ...value, references: [] }, [slot]), /linked/);
    assert.throws(() => m.compilePrompt({ ...value, prompt: "changed" }, [slot]), /linked/);
    assert.throws(() => m.compilePrompt({ ...value, mentions: [...value.mentions, value.mentions[0]] }, [slot]), /linked/);
});
test("request builder compiles selected spans but passes raw unselected @text unchanged with structured IDs", () => {
    const controls = { text_to_media: true, aspect_ratios: [], resolutions: [], duration_seconds: null, audio: false, count: { min: 1, max: 1 }, layout_types: ["single"], quality_profiles: [], styles: [] };
    for (const [media, input, role] of [["image", slot, "character"], ["video", video, "first_frame"]]) {
        const model = { id: "model", available: true, supported_interface_levels: ["easy"], selectable_in: ["easy"], capabilities: controls, technical: { reference_inputs: [input] } };
        const caps = { interface_levels: { easy: {} }, media_types: [{ id: media, available: true, purposes: [], providers: [{ available: true, models: [model] }] }] };
        const value = insert(draft("Use @hero"), artifact(), role, [input]);
        const request = buildMediaRequest(caps, { ...INITIAL_FORM, media, ...value });
        assert.equal(request.intent.description, m.compilePrompt(value, [input]));
        assert.doesNotMatch(request.intent.description, /private-uuid|@hero/);
        assert.deepEqual(request.references, r.structuredReferences(value.references));
        assert.equal(buildMediaRequest(caps, { ...INITIAL_FORM, media, prompt: "@unselected" }).intent.description, "@unselected");
    }
});

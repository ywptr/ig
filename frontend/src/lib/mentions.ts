import { attachArtifact, attachmentError, canAttach, type Attachment } from "./references";
import type { Artifact, ReferenceInputSlot } from "../types/media";

// Offsets are textarea UTF-16 offsets, never identities. Role/strength live only
// on the linked IG#0004 attachment, so editing a card updates every mention.
export interface ArtifactMention {
    mention_id: string; artifact_id: string; display_label: string; start: number; end: number;
}
export interface MentionDraft { prompt: string; mentions: ArtifactMention[]; references: Attachment[] }
export interface MentionTrigger { start: number; end: number; query: string }
export function activeMention(prompt: string, caret: number, mentions: ArtifactMention[]): MentionTrigger | null {
    if (mentions.some(mention => caret > mention.start && caret <= mention.end)) return null;
    const match = /(?:^|[\s(])@([^@\s]*)$/.exec(prompt.slice(0, caret));
    return match ? { start: caret - match[1].length - 1, end: caret, query: match[1] } : null;
}
function pruneMentionAttachments(references: Attachment[], mentions: ArtifactMention[]) {
    return references.filter(ref => ref.source !== "mention" || mentions.some(mention => mention.artifact_id === ref.artifact_id));
}
export function editPrompt(draft: MentionDraft, prompt: string, caret?: number): MentionDraft {
    if (draft.prompt === prompt) return draft;
    const delta = prompt.length - draft.prompt.length;
    let start = 0;
    let oldEnd = draft.prompt.length, newEnd = prompt.length;
    // The post-edit caret disambiguates edits among identical visible labels.
    if (caret !== undefined && caret - delta >= 0 && caret - delta <= draft.prompt.length
        && draft.prompt.slice(caret - delta) === prompt.slice(caret)) {
        oldEnd = caret - delta; newEnd = caret;
    } else {
        while (oldEnd > 0 && newEnd > 0 && draft.prompt[oldEnd - 1] === prompt[newEnd - 1]) { oldEnd--; newEnd--; }
    }
    while (start < Math.min(oldEnd, newEnd) && draft.prompt[start] === prompt[start]) start++;
    const mentions = draft.mentions.flatMap(mention => {
        if (mention.end <= start) return [mention];
        if (mention.start >= oldEnd) return [{ ...mention, start: mention.start + delta, end: mention.end + delta }];
        return []; // Editing inside a selected token makes it ordinary text.
    });
    return { prompt, mentions, references: pruneMentionAttachments(draft.references, mentions) };
}
export function updateAttachments(draft: MentionDraft, references: Attachment[]): MentionDraft {
    // Card removal leaves the readable label as ordinary text, with no authority.
    return { ...draft, references, mentions: draft.mentions.filter(mention => references.some(ref => ref.artifact_id === mention.artifact_id)) };
}
export function mentionChoices(artifact: Artifact, references: Attachment[], slots: ReferenceInputSlot[]) {
    const existing = references.find(ref => ref.artifact_id === artifact.artifact_id);
    if (existing) return slots.filter(slot => slot.roles.includes(existing.role) && !attachmentError(existing, [slot]))
        .map(slot => ({ slot, role: existing.role }));
    return slots.filter(slot => canAttach(artifact, references, slot)).flatMap(slot => slot.roles.map(role => ({ slot, role })));
}
export function insertMention(draft: MentionDraft, trigger: MentionTrigger, artifact: Artifact,
    slots: ReferenceInputSlot[], role: string, mention_id: string): MentionDraft {
    const choice = mentionChoices(artifact, draft.references, slots).find(choice => choice.role === role);
    if (!choice) throw new Error("This artifact or role is not eligible for the current reference inputs.");
    const existing = draft.references.find(ref => ref.artifact_id === artifact.artifact_id);
    const references = existing ? draft.references : attachArtifact(artifact, draft.references, choice.slot, role)
        .map(ref => ref.artifact_id === artifact.artifact_id ? { ...ref, source: "mention" as const } : ref);
    // Labels are snapshots, never looked up to recover an Artifact ID.
    const display_label = (artifact.filename || "artifact").replace(/[\r\n\t]/g, " ").slice(0, 120);
    const token = `@${display_label}`;
    const prompt = draft.prompt.slice(0, trigger.start) + token + " " + draft.prompt.slice(trigger.end);
    const edited = editPrompt({ ...draft, references }, prompt, trigger.start + token.length + 1);
    return { prompt, references, mentions: [...edited.mentions, { mention_id, artifact_id: artifact.artifact_id,
        display_label, start: trigger.start, end: trigger.start + token.length }].sort((a, b) => a.start - b.start) };
}
export function mentionDetails(mention: ArtifactMention, references: Attachment[]) {
    const ref = references.find(ref => ref.artifact_id === mention.artifact_id);
    return { ...mention, role: ref?.role, strength: ref?.strength };
}
export function mentionErrors(draft: MentionDraft, slots: ReferenceInputSlot[]): string[] {
    let previousEnd = 0;
    const ids = new Set<string>();
    return draft.mentions.flatMap(mention => {
        const ref = draft.references.find(ref => ref.artifact_id === mention.artifact_id);
        const invalid = ids.has(mention.mention_id) || mention.start < previousEnd || mention.start < 0
            || draft.prompt.slice(mention.start, mention.end) !== `@${mention.display_label}` || !ref;
        previousEnd = mention.end; ids.add(mention.mention_id);
        return invalid ? ["A selected mention is no longer linked to its attachment. Edit or remove it."]
            : attachmentError(ref!, slots) ? ["A mentioned reference is incompatible with the current selection. Update its card or remove it."] : [];
    });
}
export function compilePrompt(draft: MentionDraft, slots: ReferenceInputSlot[]): string {
    const errors = mentionErrors(draft, slots);
    if (errors.length) throw new Error(errors[0]);
    let result = draft.prompt;
    for (const mention of [...draft.mentions].reverse()) {
        const index = draft.references.findIndex(ref => ref.artifact_id === mention.artifact_id);
        const ref = draft.references[index];
        const label = ref.role === "first_frame" ? `first frame (reference image ${index + 1})` : `reference ${ref.artifact.media_type} ${index + 1}`;
        result = result.slice(0, mention.start) + label + result.slice(mention.end);
    }
    return result.trim();
}

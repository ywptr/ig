import type { Artifact, ReferenceInputSlot } from "../types/media";

export interface Attachment { artifact: Artifact; artifact_id: string; role: string; strength?: "loose" | "normal" | "strong" }
export function compatible(artifact: Artifact, slot: ReferenceInputSlot) {
    return slot.media_types.includes(artifact.media_type) && slot.mime_types.includes(artifact.mime_type ?? "")
        && (artifact.size_bytes === null || (artifact.size_bytes > 0 && artifact.size_bytes <= slot.max_bytes));
}
// Auto is conservative across every possible model, without predicting routing.
export function sharedReferenceSlots(groups: ReferenceInputSlot[][]): ReferenceInputSlot[] {
    if (!groups.length) return [];
    return groups[0].flatMap(slot => {
        const roles = slot.roles.filter(role => groups.every(group => group.some(other => other.roles.includes(role))));
        if (!roles.length) return [];
        // Match by role rather than slot name; preserve aggregate count bounds.
        const others = groups.flatMap(group => group.filter(other => other.roles.some(role => roles.includes(role))));
        const media_types = slot.media_types.filter(media => others.every(other => other.media_types.includes(media)));
        const mime_types = slot.mime_types.filter(mime => others.every(other => other.mime_types.includes(mime)));
        if (!media_types.length || !mime_types.length) return [];
        return [{ ...slot, roles, media_types, mime_types, min_count: Math.max(...others.map(s => s.min_count)),
            max_count: Math.min(...others.map(s => s.max_count)), max_bytes: Math.min(...others.map(s => s.max_bytes)),
            strengths: slot.strengths.filter(strength => others.every(other => other.strengths.includes(strength))) }];
    });
}
export function attachmentError(reference: Attachment, slots: ReferenceInputSlot[]): string | null {
    const slot = slots.find(slot => slot.roles.includes(reference.role));
    if (!slot) return "This role is unavailable for the current selection. Change the role or remove this reference.";
    if (!compatible(reference.artifact, slot)) return "This artifact’s media, MIME type or size is incompatible. Remove this reference.";
    if (reference.strength !== undefined && !slot.strengths.includes(reference.strength)) return "This strength is unavailable. Clear or change it.";
    return null;
}
export function referenceErrors(references: Attachment[], slots: ReferenceInputSlot[]): string[] {
    const errors = references.flatMap(ref => { const error = attachmentError(ref, slots); return error ? [error] : []; });
    for (const slot of slots) {
        const count = references.filter(ref => slot.roles.includes(ref.role)).length;
        if (count < slot.min_count) errors.push(`${slot.name}: attach at least ${slot.min_count} reference(s).`);
        if (count > slot.max_count) errors.push(`${slot.name}: at most ${slot.max_count} reference(s). Remove extras.`);
    }
    return errors;
}
export function canAttach(artifact: Artifact, references: Attachment[], slot: ReferenceInputSlot) {
    return compatible(artifact, slot) && !references.some(ref => ref.artifact_id === artifact.artifact_id)
        && references.filter(ref => slot.roles.includes(ref.role)).length < slot.max_count;
}
export function attachArtifact(artifact: Artifact, references: Attachment[], slot: ReferenceInputSlot, role = slot.roles[0]): Attachment[] {
    if (!slot.roles.includes(role) || !canAttach(artifact, references, slot)) throw new Error("This artifact cannot be attached to the selected input.");
    return [...references, { artifact, artifact_id: artifact.artifact_id, role,
        ...(slot.strengths.length ? { strength: slot.strengths.includes("normal") ? "normal" as const : slot.strengths[0] } : {}) }];
}
export function removeAttachment(references: Attachment[], id: string) { return references.filter(ref => ref.artifact_id !== id); }
export function structuredReferences(references: Attachment[]) {
    return references.map(({ artifact_id, role, strength }) => ({ artifact_id, role, ...(strength !== undefined ? { strength } : {}) }));
}
export const UPLOAD_MIMES = ["image/png", "image/jpeg", "image/webp"];
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export function validateUpload(file: Pick<File, "type" | "size">, slot: ReferenceInputSlot) {
    if (!UPLOAD_MIMES.includes(file.type) || !slot.mime_types.includes(file.type) || !slot.media_types.includes("image"))
        throw new Error("Choose a supported PNG, JPEG or WebP image for this input.");
    if (!file.size) throw new Error("The file is empty.");
    if (file.size > Math.min(MAX_UPLOAD_BYTES, slot.max_bytes)) throw new Error(`The file exceeds the ${Math.min(MAX_UPLOAD_BYTES, slot.max_bytes) / 1024 / 1024} MiB limit.`);
}

import { useId, useRef, useState } from "react";
import { activeMention, editPrompt, insertMention, mentionDetails, mentionErrors, type MentionDraft, type MentionTrigger } from "../lib/mentions";
import type { Artifact, ReferenceInputSlot } from "../types/media";
import { ArtifactAutocomplete, type AutocompleteHandle } from "./ArtifactAutocomplete";

interface Props { draft: MentionDraft; slots: ReferenceInputSlot[]; onChange: (draft: MentionDraft) => void }
export function ArtifactPromptEditor({ draft, slots, onChange }: Props) {
    const editor = useRef<HTMLTextAreaElement>(null);
    const autocomplete = useRef<AutocompleteHandle>(null);
    const [trigger, setTrigger] = useState<MentionTrigger | null>(null);
    const [activeId, setActiveId] = useState<string | undefined>();
    const listId = useId();
    const helpId = useId();
    const open = !!trigger && slots.length > 0;
    function close() { setTrigger(null); editor.current?.focus(); }
    function select(artifact: Artifact, role: string) {
        if (!trigger) return;
        const next = insertMention(draft, trigger, artifact, slots, role, crypto.randomUUID());
        onChange(next); setTrigger(null);
        const caret = next.mentions.find(mention => mention.start === trigger.start)!.end + 1;
        requestAnimationFrame(() => { editor.current?.focus(); editor.current?.setSelectionRange(caret, caret); });
    }
    const errors = mentionErrors(draft, slots);
    return <div className="artifact-prompt-editor" onBlur={event => {
        if (!event.currentTarget.contains(event.relatedTarget)) setTrigger(null);
    }}>
        <label className="lab-prompt">Prompt<textarea ref={editor} required rows={4} value={draft.prompt}
            placeholder="Describe what you want to generate… Type @ to select a reference."
            aria-autocomplete="list" aria-haspopup="listbox" aria-controls={open ? listId : undefined}
            aria-expanded={open} aria-activedescendant={open ? activeId : undefined} aria-describedby={helpId}
            onChange={event => {
                const next = editPrompt(draft, event.target.value, event.target.selectionStart);
                onChange(next); setTrigger(activeMention(next.prompt, event.target.selectionStart, next.mentions));
            }} onClick={event => setTrigger(activeMention(draft.prompt, event.currentTarget.selectionStart, draft.mentions))}
            onKeyDown={event => { if (open) autocomplete.current?.keyDown(event); }} /></label>
        <p id={helpId} className="lab-help">Type @ to search references. Select a suggestion to link it; typed names alone stay plain text. Edit roles and strengths on the reference cards.</p>
        {open && <ArtifactAutocomplete key={JSON.stringify([trigger, slots])} ref={autocomplete} query={trigger.query}
            slots={slots} references={draft.references} listId={listId} onChoose={select} onClose={close} onActive={setActiveId} />}
        {draft.mentions.length > 0 && <div className="prompt-mentions" aria-label="Selected prompt mentions">
            {draft.mentions.map(mention => { const details = mentionDetails(mention, draft.references);
                const invalid = mentionErrors({ ...draft, mentions: [mention] }, slots).length > 0;
                return <span className={`prompt-mention${invalid ? " invalid" : ""}`} key={mention.mention_id}>
                    @{mention.display_label} · {details.role?.replaceAll("_", " ") ?? "Missing attachment"}{details.strength ? ` · ${details.strength}` : ""}
                    {invalid ? " · check reference" : " · linked"}
                </span>;
            })}
        </div>}
        {errors.length > 0 && <p role="alert" className="error">{errors[0]}</p>}
    </div>;
}

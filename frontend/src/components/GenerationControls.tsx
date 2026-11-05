import type { GenerationCapabilities, MediaType } from "../types/media";
import { humanize, type LabForm } from "../lib/generation";

interface Props {
    media: MediaType; controls: GenerationCapabilities; purposes: string[];
    values: LabForm; onChange: (patch: Partial<LabForm>) => void;
}
export function GenerationControls({ media, controls, purposes, values, onChange }: Props) {
    return <div className="lab-controls">
        {media === "image" && purposes.length > 0 && <label>Purpose
            <select value={values.purpose} onChange={event => onChange({ purpose: event.target.value })}>
                {purposes.map(value => <option key={value} value={value}>{humanize(value)}</option>)}
            </select>
        </label>}
        {controls.aspect_ratios.length > 0 && <label>Aspect ratio
            <select value={values.aspect} onChange={event => onChange({ aspect: event.target.value })}>
                {controls.aspect_ratios.map(value => <option key={value}>{value}</option>)}
            </select>
        </label>}
        {media === "image" && values.mode !== "easy" && <>
            {controls.quality_profiles.length > 0 && <label>Quality
                <select value={values.quality} onChange={event => onChange({ quality: event.target.value })}>
                    {controls.quality_profiles.map(value => <option key={value} value={value}>{humanize(value)}</option>)}
                </select>
            </label>}
            {controls.styles.length > 0 && <label>Style
                <select value={values.style} onChange={event => onChange({ style: event.target.value })}>
                    {controls.styles.map(value => <option key={value} value={value}>{humanize(value)}</option>)}
                </select>
            </label>}
            {controls.count.max > controls.count.min && <label>Count
                <input type="number" min={controls.count.min} max={controls.count.max} step={1} value={values.count ?? ""}
                    onChange={event => onChange({ count: event.target.value === "" ? null : Number(event.target.value) })} />
            </label>}
        </>}
        {media === "video" && <>
            {controls.resolutions.length > 0 && <label>Resolution
                <select value={values.resolution} onChange={event => onChange({ resolution: event.target.value })}>
                    {controls.resolutions.map(value => <option key={value}>{value}</option>)}
                </select>
            </label>}
            {controls.duration_seconds && <label>Duration (seconds)
                <input type="number" required min={controls.duration_seconds.min} max={controls.duration_seconds.max} step={1}
                    value={values.duration ?? ""} onChange={event => onChange({ duration: event.target.value === "" ? null : Number(event.target.value) })} />
            </label>}
            {controls.audio && <label className="lab-check"><input type="checkbox" checked={values.audio}
                onChange={event => onChange({ audio: event.target.checked })} /> Generate audio</label>}
        </>}
    </div>;
}

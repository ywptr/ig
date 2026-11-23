import type { SpeechCapabilities, SpeechOptions } from "../types/media";

export function SpeechControls({ capabilities, values, onChange }: {
    capabilities: SpeechCapabilities;
    values: SpeechOptions;
    onChange: (next: SpeechOptions) => void;
}) {
    return <div className="lab-controls">
        <label>Voice<select value={values.voice ?? capabilities.default_voice}
            onChange={event => onChange({ ...values, voice: event.target.value })}>
            {capabilities.voices.map(voice => <option key={voice.id} value={voice.id}>{voice.label}</option>)}
        </select></label>
        <label>Audio format<select value={values.output_format ?? capabilities.default_format}
            onChange={event => onChange({ ...values, output_format: event.target.value })}>
            {capabilities.formats.map(format => <option key={format.id} value={format.id}>{format.id.toUpperCase()}</option>)}
        </select></label>
    </div>;
}

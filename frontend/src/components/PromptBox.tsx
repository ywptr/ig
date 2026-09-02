interface PromptBoxProps {
    prompt: string;
    onPromptChange: (value: string) => void;
    onGenerate: () => void;
    generating: boolean;
}

export default function PromptBox({
    prompt,
    onPromptChange,
    onGenerate,
    generating,
}: PromptBoxProps) {

    function handleKeyDown(
        event: React.KeyboardEvent<HTMLTextAreaElement>
    ) {

        if (
            event.key === "Enter" &&
            (event.ctrlKey || event.metaKey)
        ) {

            event.preventDefault();

            onGenerate();
        }
    }

    return (
        <div className="prompt-box">

            <textarea
                value={prompt}
                onChange={(event) =>
                    onPromptChange(
                        event.target.value
                    )
                }
                onKeyDown={handleKeyDown}
                placeholder={
                    "A cinematic photograph of..."
                }
                disabled={generating}
            />

            <div className="prompt-footer">

                <span className="keyboard-hint">
                    Ctrl + Enter to generate
                </span>

                <button
                    className="generate-button"
                    onClick={onGenerate}
                    disabled={
                        generating ||
                        !prompt.trim()
                    }
                >
                    {generating
                        ? "Generating…"
                        : "✨ Generate image"}
                </button>

            </div>

        </div>
    );
}

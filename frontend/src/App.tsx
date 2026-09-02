import { useEffect, useState, } from "react";

import PromptBox from "./components/PromptBox";
import ImageGrid from "./components/ImageGrid";
import type { Image } from "./components/ImageCard";

import {
    getImages,
    generateImage,
} from "./api/images";

function App() {
    const [prompt, setPrompt] = useState("");

    const [images, setImages] =
        useState<Image[]>([]);

    const [generating, setGenerating] =
        useState(false);

    const [error, setError] =
        useState<string | null>(null);

    function hasGeneratingImages(images: Image[]) {
        return images.some(
            (image) => image.status === "generating"
        );
    }

    async function loadHistory() {

        try {

            setError(null);

            const result =
                await getImages();

            setImages(result);

        } catch (error) {

            setError(
                error instanceof Error
                    ? error.message
                    : "Failed to load history"
            );
        }
    }


    async function handleGenerate() {
        const value = prompt.trim();

        if (!value || generating) {
            return;
        }

        try {
            setGenerating(true);
            setError(null);

            const result = await generateImage(value);

            console.log(
                "Generation accepted:",
                result
            );

            setPrompt("");

            await loadHistory();

        } catch (error) {
            setError(
                error instanceof Error
                    ? error.message
                    : "Generation failed"
            );
        } finally {
            setGenerating(false);
        }
    }


    useEffect(() => {
        loadHistory();
    }, []);

    useEffect(() => {
        if (!hasGeneratingImages(images)) {
            return;
        }

        const interval = window.setInterval(() => {
            loadHistory();
        }, 3000);

        return () => {
            window.clearInterval(interval);
        };
    }, [images]);

    return (
        <div className="app">

            <header className="app-header">

                <div className="header-inner">

                    <div className="brand">
                        <span className="brand-name">
                            IG
                        </span>

                        <span className="brand-subtitle">
                            AI Image Generator
                        </span>
                    </div>

                </div>

            </header>

             <main className="main">

                <section className="hero">

                    <div className="hero-heading">

                        <h1>
                            Create an image
                        </h1>

                        <p>
                            Describe what you want to generate.
                        </p>

                    </div>


                    <PromptBox
                        prompt={prompt}
                        onPromptChange={setPrompt}
                        onGenerate={handleGenerate}
                        generating={generating}
                    />


                    {generating && (
                        <div className="generation-status">
                            Generating image…
                            This may take a little while.
                        </div>
                    )}


                    {error && (
                        <div className="error">
                            {error}
                        </div>
                    )}

                </section>


                <section className="history-section">

                    <div className="section-heading">

                        <h2>
                            History
                        </h2>

                        <span className="image-count">
                            {images.length}
                            {" "}
                            {images.length === 1
                                ? "image"
                                : "images"}
                        </span>

                    </div>


                    <ImageGrid
                        images={images}
                    />

                </section>
            </main>
        </div>
    );
}

export default App;
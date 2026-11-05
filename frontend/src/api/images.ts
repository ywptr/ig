import type { Image } from "../components/ImageCard";
import { requestJSON } from "./client";

export function getImages(signal?: AbortSignal): Promise<Image[]> {
    return requestJSON("/v1/images", { signal });
}

export async function getImage(
    requestId: string
): Promise<Image> {
    const response = await fetch(
        `/v1/images/${requestId}`
    );

    if (!response.ok) {
        throw new Error(
            "Failed to load image request"
        );
    }

    return response.json();
}


export async function generateImage(
    prompt: string
): Promise<Image> {
    const response = await fetch(
        "/v1/images/generations",
        {
            method: "POST",

            headers: {
                "Content-Type": "application/json",
            },

            body: JSON.stringify({
                prompt,
            }),
        }
    );

    if (!response.ok) {
        let message = "Image generation failed";

        try {
            const error = await response.json();
            message = error.detail || message;
        } catch {
            // Ignore non-JSON error responses.
        }

        throw new Error(message);
    }

    return response.json();
}

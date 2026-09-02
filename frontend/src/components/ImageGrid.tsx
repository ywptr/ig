import ImageCard, {
    type Image,
} from "./ImageCard";

interface ImageGridProps {
    images: Image[];
}

export default function ImageGrid({
    images,
}: ImageGridProps) {
    if (images.length === 0) {
        return (
            <p className="empty">
                No images yet.
            </p>
        );
    }

    return (
        <section className="history">
            {images.map((image) => (
                <ImageCard
                    key={image.request_id}
                    image={image}
                />
            ))}
        </section>
    );
}
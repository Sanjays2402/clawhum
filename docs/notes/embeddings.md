# Embedding notes

CLAP outputs a 512-d vector. We L2-normalize before indexing so inner-product
search equals cosine. Track-side we average across the first 8 fixed windows.
Query-side we take a single window (full hum, max 10 s).

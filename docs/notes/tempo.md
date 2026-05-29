# Tempo rerank notes

We score tempo proximity as `min(a,b)/max(a,b)`. We also test half/double
matches and take the best. This handles hums sung at half the song's BPM.

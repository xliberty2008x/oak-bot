# Source-preserving compositing

For foreground layers recovered from flattened artwork, derive coverage from guide-identified stems and attached object contours, not shared pigments or guide rectangles. Solid interiors occlude; genuine negative spaces remain transparent; internal veins are not holes. Preserve original boundary pixels and restore source scene pixels only within verified foreground coverage. Closing and flood-fill can bridge real gaps: verify their result against local contours rather than assuming connected regions are foreground.

Before batch rendering, test a difficult crossing using aligned source, transformed unoccluded sprite and composite crops, with a thin mask-boundary overlay. Check neighboring frames and both sides of the boundary: original detail must remain visible through genuine gaps and disappear within solid occluders, respecting the requested layer order. These are internal diagnostics, not added deliverables. A filled mask preview or favorable sampled review alone is insufficient.

Reserve work for export and delivery. If detail is already missing in a raw frame, repair compositing first; regenerate affected frames, reopen the actual export, verify the repaired crossing and requested dimensions/timing, then deliver that checked file.

# Procedural illustration animation

`oak.animation.Animation` creates a bounded local 2D animation candidate with
Pillow and the installed FFmpeg. It uses no provider, GPU quota or paid service.
It does not synthesize motion with a generative model or automatically locate
eyes/hair in arbitrary pictures.

The native agent prepares the source, aligned closed-eye artwork, one or two eye
rectangles and one to four simple hair polygons. This technical preparation is
the agent's responsibility, not a form the child must fill out. Closed-eye
artwork should preserve the source texture, shading, linework and lid shape;
generic flat eyelid patches are not an acceptable substitute. Existing image
editing can prepare the artwork, after which the agent composites only the eye
patches onto the original source. The helper rejects changes outside the selected
eye rectangles, mismatched sizes, transparency, overlapping regions, invalid
polygons and excessive displacement.

Inputs are opaque single-frame images with even dimensions, 64–1024 pixels per
side. Eye bounds are `[left, top, right, bottom]`, with exclusive right/bottom.
Hair polygons contain integer `[x, y]` vertices. Images/geometry are snapshotted
in memory before rendering. Eye closure reveals the supplied closed artwork
from top and bottom; hair uses a small periodic local warp. The source outside
the selected regions remains pixel-identical in every rendered frame. There is
no camera transform. First and final rendered frames exactly equal the source.

`Animation(source_image, closed_image, eyes, hair_polygons, seconds=3, fps=24,
amplitude=3).encode(new_mp4_path)` produces a silent H.264 MP4, plus a receipt
labelled `procedural_2d` and `review_required: true`. Duration is 1–6 seconds at
12–30 fps and hair displacement is 1–12 pixels. H.264 is lossy: unchanged source
pixels and the loop seam require codec-tolerance checks after decoding. Encoding
and publication are local, bounded and never overwrite an existing output.

Independent review of both the original image and actual decoded video is
required before delivery. It must establish the requested blinking and hair
motion, preserved appearance/clothing, coherent lid texture, correct occlusion
and a usable loop. Numeric pixel differences prove neither semantics nor craft.
The existing review gate must return `pass` for the exact immutable candidate;
`revise` and `unverified` must not deliver it. Generation alone never sends a file.
Generated candidates retain a durable conversation-scoped binding from both
candidate path and exact output SHA-256 to the source path, original SHA-256 and
fixed acceptance criteria. A later turn or gateway
restart cannot erase that requirement, and producer-supplied review arguments
cannot replace it. A changed/missing source requires restoration or a new
candidate. Disabling quality review prevents delivery of these bound candidates.
Identical copied/renamed bytes retain the binding, including under a different
extension; output identity is checked on an immutable gateway snapshot before
delivery. This is exact-byte identity, not a watermark: edited/re-encoded copies
still rely on the general independent review against the owner's task.

The native `oak_animate_illustration` tool takes `image_path`,
`closed_eyes_path`, `eye_regions`, `hair_polygons` and optional `seconds`, `fps`,
`amplitude`. It requires the configured execution worker and `quality_review`.
The worker reads held regular-file descriptors and verifies gateway snapshot
digests before decoding. Native generated artwork is staged through the existing
image transfer namespace. The result is independently reviewed immediately,
returns its candidate path and review result, and never delivers automatically.
Only a subsequent successful `oak_send_file` may send the reviewed snapshot.

Focused tests use an explicitly authored synthetic illustrated character. They
check exact outside-region pixels, distinct open/intermediate/closed states,
local hair movement and loop endpoints, plus an actual FFmpeg encode/decode with
fixture-specific codec tolerances. These checks do not prove suitability for the
unavailable original child attachment or for arbitrary photographs. Unsupported
geometry or failed visual review requires repair or an honest limitation.

# Public Space capability observations

`oak.hf_spaces.Spaces` provides anonymous, GET-only discovery independently of
the optional Hugging Face connector. `search(query, limit=5)` uses the public
Hub listing API. `inspect(owner_space)` observes the selected public Space's
revision, runtime, Gradio configuration and published Image → Video endpoints.
It never uploads a file, starts generation, wakes a sleeping Space, logs in,
creates a Space or purchases anything. No additional dependency is needed.

The helper accepts only public, ungated Spaces and HTTPS hosts under `hf.space`
obtained from that Space's Hub metadata. It refuses redirects and authenticated
pages. Inputs, response sizes, endpoint counts and request timeouts are bounded.
It projects fixed fields rather than forwarding model cards, API examples or
provider errors as instructions. Unsupported schemas remain unavailable.

`RUNNING` and an Image → Video declaration establish only observed capability.
Cost, quota remaining, queue position and successful generation remain explicitly
unknown. A queue-enabled flag does not establish a wait time. The helper does
not authorize generation, and callers must not treat its `observed` status as
permission to spend money or proof a task can finish.

On 2026-10-18, an anonymous public GET observed
`Saravutw/WAN2.2_I2V_LIGHTNING_4-8step_custom` at revision
`fca7f5a33da3057bae497de5cd70f49ce75e6c02`, RUNNING, with a Gradio 6.0.2
`/generate_video` endpoint taking an Image and returning Video. These are
time-specific observations, not a supported-provider promise. No generation or
account/quota check was performed; the original issue attachment was unavailable.

The official [Hub client reference](https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api#huggingface_hub.HfApi.list_spaces)
documents listing/search and the separate authenticated quota method. The
[ZeroGPU documentation](https://huggingface.co/docs/hub/spaces-zerogpu)
describes limited free use and account-dependent quota; it also describes paid
overage for some account tiers. Anonymous metadata cannot prove remaining quota.
The [Gradio client documentation](https://www.gradio.app/main/docs/python-client/introduction)
distinguishes published API inspection from submitting a job and then observing
its queue status. No generation API is called by this module.

This module is initially a read model. Native tool/executor integration and any
generation route are separate changes; the existing browser upload and artifact
quality review contracts remain in force.

import configparser
import os

from src.defaults import UPSCALE_DEFAULTS
from src.image_gen.ImageWorkflow import *
from src.image_gen.sd_workflows import *
from src.util import get_loras_from_prompt

model_type_to_workflow = {ModelType.SD15: SD15Workflow, ModelType.SDXL: SDXLWorkflow, ModelType.CASCADE: SDCascadeWorkflow}

config = configparser.ConfigParser()
config.read("config.properties")
comfy_root_directory = config["LOCAL"]["COMFY_ROOT_DIR"]


async def _do_txt2img(params: ImageWorkflow, model_type: ModelType, loras: list[Lora]):
    workflow = model_type_to_workflow[model_type](params.model, params.clip_skip, loras)
    workflow.create_latents(params.dimensions, params.batch_size)
    workflow.condition_prompts(params.prompt, params.negative_prompt or "")
    workflow.sample(params.seed, params.num_steps, params.cfg_scale, params.sampler, params.scheduler or "normal")
    images = workflow.decode_and_save("final_output")
    results = await images._wait()
    image_batch = [await results.get(i) for i in range(params.batch_size)]
    return image_batch


async def _do_img2img(params: ImageWorkflow, model_type: ModelType, loras: list[Lora]):
    workflow = model_type_to_workflow[model_type](params.model, params.clip_skip, loras)
    image_input = LoadImage(params.filename)[0]
    workflow.create_img2img_latents(image_input, params.batch_size)
    if params.inpainting_prompt:
        workflow.mask_for_inpainting(image_input, params.inpainting_prompt, params.inpainting_detection_threshold)
    workflow.condition_prompts(params.prompt, params.negative_prompt or "")
    workflow.sample(params.seed, params.num_steps, params.cfg_scale, params.sampler, params.scheduler or "normal", params.denoise_strength)
    images = workflow.decode_and_save("final_output")
    results = await images._wait()
    image_batch = [await results.get(i) for i in range(params.batch_size)]
    return image_batch


async def _do_upscale(params: ImageWorkflow, model_type: ModelType, loras: list[Lora]):
    workflow = UpscaleWorkflow()
    workflow.load_image(params.filename)
    workflow.upscale(UPSCALE_DEFAULTS.model, 2.0)
    image = workflow.save("final_output")
    results = await image._wait()
    return await results.get(0)


async def _do_add_detail(params: ImageWorkflow, model_type: ModelType, loras: list[Lora]):
    workflow = model_type_to_workflow[model_type](params.model, params.clip_skip, loras)
    image_input = LoadImage(params.filename)[0]
    workflow.create_img2img_latents(image_input, params.batch_size)
    workflow.condition_prompts(params.prompt, params.negative_prompt or "")
    workflow.sample(params.seed, params.num_steps, params.cfg_scale, params.sampler, params.scheduler or "normal", params.denoise_strength)
    images = workflow.decode_and_save("final_output")
    results = await images._wait()
    image_batch = [await results.get(i) for i in range(params.batch_size)]
    return image_batch


async def _do_image_mashup(params: ImageWorkflow, model_type: ModelType, loras: list[Lora]):
    workflow = model_type_to_workflow[model_type](params.model, params.clip_skip, loras)
    image_inputs = [LoadImage(filename)[0] for filename in [params.filename, params.filename2]]
    workflow.create_latents(params.dimensions, params.batch_size)
    workflow.condition_prompts(params.prompt, params.negative_prompt or "")
    workflow.unclip_encode(image_inputs)
    workflow.sample(params.seed, params.num_steps, params.cfg_scale, params.sampler, params.scheduler or "normal")
    images = workflow.decode_and_save("final_output")
    results = await images._wait()
    image_batch = [await results.get(i) for i in range(params.batch_size)]
    return image_batch


async def _do_video(params: ImageWorkflow, model_type: ModelType, loras: list[Lora]):
    import PIL

    with open(params.filename, "rb") as f:
        image = PIL.Image.open(f)
        width = image.width
        height = image.height
        padding = 0
        if width / height <= 1:
            padding = height // 2

    image = LoadImage(params.filename)[0]
    image, _ = ImagePadForOutpaint(image, padding, 0, padding, 0, 40)
    model, clip_vision, vae = ImageOnlyCheckpointLoader(params.model)
    model = VideoLinearCFGGuidance(model, params.min_cfg)
    positive, negative, latent = SVDImg2vidConditioning(clip_vision, image, vae, 1024, 576, 25, params.motion, 8, params.augmentation)
    latent = KSampler(model, params.seed, params.num_steps, params.cfg_scale, params.sampler, params.scheduler, positive, negative, latent, 1)
    image2 = VAEDecode(latent, vae)
    video = VHSVideoCombine(image2, 8, 0, "final_output", "image/gif", False, True, None, None)
    preview = PreviewImage(image)
    await preview._wait()
    await video._wait()
    results = video.wait()._output
    final_video = PIL.Image.open(os.path.join(comfy_root_directory, "output", results["gifs"][0]["filename"]))
    return [final_video]


workflow_type_to_method = {
    WorkflowType.txt2img: _do_txt2img,
    WorkflowType.img2img: _do_img2img,
    WorkflowType.upscale: _do_upscale,
    WorkflowType.add_detail: _do_add_detail,
    WorkflowType.image_mashup: _do_image_mashup,
    WorkflowType.video: _do_video,
}


async def do_workflow(params: ImageWorkflow):
    loras = [Lora(lora, strength) for lora, strength in zip(params.loras, params.lora_strengths)] if params.loras else []

    extra_loras = get_loras_from_prompt(params.prompt)
    loras.extend([Lora(f"{lora[0]}.safetensors", lora[1]) for lora in extra_loras])

    if params.use_accelerator_lora:
        loras.append(Lora(params.accelerator_lora_name, 1.0))

    return await workflow_type_to_method[params.workflow_type](params, params.model_type, loras)

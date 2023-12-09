import discord
import discord.ext
import configparser
import os
import random

from discord import app_commands
from discord.app_commands import Choice, Range

from buttons import Buttons
from imageGen import (
    ImageWorkflow,
    generate_images,
    get_models,
    get_loras,
    get_samplers,
)
from collage_utils import create_collage
from consts import *


def setup_config():
    if not os.path.exists("config.properties"):
        generate_default_config()

    if not os.path.exists("./out"):
        os.makedirs("./out")

    config = configparser.ConfigParser()
    config.read("config.properties")
    return config["BOT"]["TOKEN"], config["BOT"]["SDXL_SOURCE"]


def generate_default_config():
    config = configparser.ConfigParser()
    config["DISCORD"] = {"TOKEN": "YOUR_DEFAULT_DISCORD_BOT_TOKEN"}
    config["LOCAL"] = {"SERVER_ADDRESS": "YOUR_COMFYUI_URL"}
    with open("config.properties", "w") as configfile:
        config.write(configfile)


# setting up the bot
TOKEN, IMAGE_SOURCE = setup_config()
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)


models = get_models()
loras = get_loras()
samplers = get_samplers()


# These aspect ratio resolution values correspond to the SDXL Empty Latent Image node.
# A latent modification node in the workflow converts it to the equivalent SD 1.5 resolution values.
ASPECT_RATIO_CHOICES = [
    Choice(name="1:1", value="1024 x 1024  (square)"),
    Choice(name="7:9 portrait", value=" 896 x 1152  (portrait)"),
    Choice(name="4:7 portrait", value=" 768 x 1344  (portrait)"),
    Choice(name="9:7 landscape", value="1152 x 896   (landscape)"),
    Choice(name="7:4 landscape", value="1344 x 768   (landscape)"),
]
SD15_MODEL_CHOICES = [Choice(name=m, value=m) for m in models[0] if "xl" not in m.lower()][:25]
SD15_LORA_CHOICES = [Choice(name=l, value=l) for l in loras[0] if "xl" not in l.lower()][:25]
SDXL_MODEL_CHOICES = [Choice(name=m, value=m) for m in models[0] if "xl" in m.lower() and "refiner" not in m.lower()][:25]
SDXL_LORA_CHOICES = [Choice(name=l, value=l) for l in loras[0] if "xl" in l.lower()][:25]
SAMPLER_CHOICES = [Choice(name=s, value=s) for s in samplers[0]]

BASE_ARG_DESCS = {
    "prompt": "Prompt for the image being generated",
    "negative_prompt": "Prompt for what you want to steer the AI away from",
    "model": "Model checkpoint to use",
    "lora": "LoRA to apply",
    "lora_strength": "Strength of LoRA",
    "aspect_ratio": "Aspect ratio of the generated image",
    "sampler": "Sampling algorithm to use",
    "num_steps": "Number of sampling steps; range [1, 20]",
    "cfg_scale": "Degree to which AI should follow prompt; range [1.0, 100.0]",
}
IMAGINE_ARG_DESCS = BASE_ARG_DESCS
SDXL_ARG_DESCS = BASE_ARG_DESCS
VIDEO_ARG_DESCS = {k: v for k, v in BASE_ARG_DESCS.items() if k != "aspect_ratio"}

BASE_ARG_CHOICES = {
    "aspect_ratio": ASPECT_RATIO_CHOICES,
    "sampler": SAMPLER_CHOICES,
}
IMAGINE_ARG_CHOICES = {
    "model": SD15_MODEL_CHOICES,
    "lora": SD15_LORA_CHOICES,
    "lora2": SD15_LORA_CHOICES,
    "lora3": SD15_LORA_CHOICES,
    **BASE_ARG_CHOICES,
}
SDXL_ARG_CHOICES = {
    "model": SDXL_MODEL_CHOICES,
    "lora": SDXL_LORA_CHOICES,
    "lora2": SDXL_LORA_CHOICES,
    "lora3": SDXL_LORA_CHOICES,
    **BASE_ARG_CHOICES,
}
VIDEO_ARG_CHOICES = {
    k: v for k, v in IMAGINE_ARG_CHOICES.items() if k not in {"lora2", "lora3", "aspect_ratio"}
}


def unpack_choices(*args):
    return [x is not None and x.value or None for x in args]


def should_filter(positive_prompt: str, negative_prompt: str) -> bool:
    positive_prompt = positive_prompt or ""
    negative_prompt = negative_prompt or ""

    config = configparser.ConfigParser()
    config.read("config.properties")
    word_list = config["BLOCKED_WORDS"]["WORDS"].split(",")
    if word_list is None:
        print("No blocked words found in config.properties")
        return False
    for word in word_list:
        if word.lower() in positive_prompt.lower() or word in negative_prompt.lower():
            return True
    return False


@tree.command(name="refresh", description="Refresh the list of models and loras")
async def slash_command(interaction: discord.Interaction):
    global models
    global loras
    models = get_models()
    loras = get_loras()
    await interaction.response.send_message("Refreshed models and loras", ephemeral=True)


@tree.command(name="imagine", description="Generate an image based on input text")
@app_commands.describe(**IMAGINE_ARG_DESCS)
@app_commands.choices(**IMAGINE_ARG_CHOICES)
async def slash_command(
    interaction: discord.Interaction,
    prompt: str,
    negative_prompt: str = None,
    model: str = None,
    lora: Choice[str] = None,
    lora_strength: float = 1.0,
    lora2: Choice[str] = None,
    lora_strength2: float = 1.0,
    lora3: Choice[str] = None,
    lora_strength3: float = 1.0,
    # enhance: bool = False,
    aspect_ratio: str = None,
    sampler: str = None,
    num_steps: Range[int, 1, 30] = None,
    cfg_scale: Range[float, 1.0, 100.0] = None,
    seed: int = None,
):
    params = ImageWorkflow(
        SD15_WORKFLOW,
        prompt,
        negative_prompt,
        model,
        unpack_choices(lora, lora2, lora3),
        [lora_strength, lora_strength2, lora_strength3],
        aspect_ratio,
        sampler,
        num_steps,
        cfg_scale,
        seed=seed,
    )
    await do_request(
        interaction,
        f'{interaction.user.mention} asked me to imagine "{prompt}", this shouldn\'t take too long...',
        f'{interaction.user.mention} asked me to imagine "{prompt}", here is what I imagined for them.',
        "imagine",
        params,
    )


@tree.command(name="video", description="Generate a video based on input text")
@app_commands.describe(**VIDEO_ARG_DESCS)
@app_commands.choices(**VIDEO_ARG_CHOICES)
async def slash_command(
    interaction: discord.Interaction,
    prompt: str,
    negative_prompt: str = None,
    model: str = None,
    lora: Choice[str] = None,
    lora_strength: float = 1.0,
    lora2: Choice[str] = None,
    lora_strength2: float = 1.0,
    lora3: Choice[str] = None,
    lora_strength3: float = 1.0,
    sampler: str = None,
    num_steps: Range[int, 1, 20] = None,
    cfg_scale: Range[float, 1.0, 100.0] = None,
    seed: int = None,
):
    params = ImageWorkflow(
        VIDEO_WORKFLOW,
        prompt,
        negative_prompt,
        model,
        unpack_choices(lora, lora2, lora3),
        [lora_strength, lora_strength2, lora_strength3],
        None,
        sampler=sampler,
        num_steps=num_steps,
        cfg_scale=cfg_scale,
        seed=seed,
    )
    await do_request(
        interaction,
        f'{interaction.user.mention} asked me to create the video "{prompt}", this shouldn\'t take too long...',
        f'{interaction.user.mention} asked me to create the video "{prompt}", here is what I created for them.',
        "video",
        params,
    )


@tree.command(name="sdxl", description="Generate an image using SDXL")
@app_commands.describe(**BASE_ARG_DESCS)
@app_commands.choices(**SDXL_ARG_CHOICES)
async def slash_command(
    interaction: discord.Interaction,
    prompt: str,
    negative_prompt: str = None,
    model: str = None,
    lora: Choice[str] = None,
    lora_strength: float = 1.0,
    lora2: Choice[str] = None,
    lora_strength2: float = 1.0,
    lora3: Choice[str] = None,
    lora_strength3: float = 1.0,
    aspect_ratio: str = None,
    sampler: str = None,
    num_steps: Range[int, 1, 20] = None,
    cfg_scale: Range[float, 1.0, 100.0] = None,
    seed: int = None,
):
    params = ImageWorkflow(
        SDXL_WORKFLOW,
        prompt,
        negative_prompt,
        model,
        unpack_choices(lora, lora2, lora3),
        [lora_strength, lora_strength2, lora_strength3],
        aspect_ratio,
        sampler=sampler,
        num_steps=num_steps,
        cfg_scale=cfg_scale,
        seed=seed,
    )
    await do_request(
        interaction,
        f'{interaction.user.mention} asked me to imagine "{prompt}", this shouldn\'t take too long...',
        f'{interaction.user.mention} asked me to imagine "{prompt}", here is what I imagined for them.',
        "sdxl",
        params,
    )


async def do_request(
    interaction: discord.Interaction,
    intro_message: str,
    completion_message: str,
    command_name: str,
    params: ImageWorkflow,
):
    if should_filter(params.prompt, params.negative_prompt):
        print(
            f"Prompt or negative prompt contains a blocked word, not generating image. Prompt: {params.prompt}, Negative Prompt: {params.negative_prompt}"
        )
        await interaction.response.send_message(
            f"The prompt {params.prompt} or negative prompt {params.negative_prompt} contains a blocked word, not generating image.",
            ephemeral=True,
        )
        return

    # Send an initial message
    await interaction.response.send_message(intro_message)

    if params.seed is None:
        params.seed = random.randint(0, 999999999999999)

    images, enhanced_prompt = await generate_images(params)

    final_message = f"{completion_message}\n Seed: {params.seed}"
    buttons = Buttons(params, images, interaction.user, command=command_name)

    fname = "collage.gif" if "GIF" in images[0].format else "collage.png"
    await interaction.channel.send(
        content=final_message, file=discord.File(fp=create_collage(images), filename=fname), view=buttons
    )

# run the bot
client.run(TOKEN)

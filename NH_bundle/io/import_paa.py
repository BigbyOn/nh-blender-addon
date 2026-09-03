# Processing functions to import a PAA texture file as an image data block.
# The actual file handling is implemented in the data_paa module.


import os
import time

import bpy

from . import data_paa as paa
from ..utilities.logger import ProcessLogger


def find_existing_image(filepath, color_space):
    filepath = os.path.abspath(bpy.path.abspath(filepath)).lower()
    is_data = color_space == 'DATA'

    for image in bpy.data.images:
        image_path = image.filepath_raw if image.filepath_raw != "" else image.filepath
        if image_path == "":
            continue

        try:
            image_path = os.path.abspath(bpy.path.abspath(image_path)).lower()
        except Exception:
            continue

        if image_path != filepath:
            continue

        try:
            image_is_data = image.colorspace_settings.is_data
        except Exception:
            image_is_data = False

        if image_is_data == is_data:
            return image

    return None


def create_image_from_texture(filepath, tex, color_space):
    alpha = tex.type == paa.PAA_Type.DXT5

    if tex.type not in (paa.PAA_Type.DXT1, paa.PAA_Type.DXT5):
        return None

    mip = tex.mips[0]
    mip.decompress(tex.type)
    swiztagg = tex.get_tagg("SWIZ")
    if swiztagg is not None:
        mip.swizzle(swiztagg.data)

    img = bpy.data.images.new(os.path.basename(filepath), mip.width, mip.height, alpha=alpha, is_data=color_space == 'DATA')
    img.filepath_raw = filepath
    if alpha:
        img.alpha_mode = 'PREMUL'
    else:
        img.alpha_mode = 'NONE'

    if color_space == 'DATA':
        try:
            img.colorspace_settings.is_data = True
        except Exception:
            try:
                img.colorspace_settings.name = 'Non-Color'
            except Exception:
                pass

    img.pixels = [value for c in zip(*mip.data) for value in c]
    img.update()
    img.pack()

    return img


def load_file(filepath, color_space='SRGB', check_existing=True):
    filepath = os.path.abspath(bpy.path.abspath(filepath))

    if check_existing:
        existing = find_existing_image(filepath, color_space)
        if existing is not None:
            return existing, None

    with open(filepath, "rb") as file:
        tex = paa.PAA_File.read(file)

    return create_image_from_texture(filepath, tex, color_space), tex


def import_file(operator, context, file):
    logger = ProcessLogger()
    logger.start_subproc("PAA import from %s" % operator.filepath)

    wm = context.window_manager
    wm.progress_begin(0, 1000)
    wm.progress_update(0)

    tex = paa.PAA_File.read(file)

    logger.start_subproc("File report:")
    logger.step("Format: %s" % tex.type.name)
    logger.step("Taggs: %d" % len(tex.taggs))
    logger.start_subproc("Mipmaps:")
    for i, mip in enumerate(tex.mips):
        wm.progress_update(i + 1)
        logger.step("%d x %d" % (mip.width, mip.height))

    logger.end_subproc()
    logger.end_subproc()

    if tex.type not in (paa.PAA_Type.DXT1, paa.PAA_Type.DXT5):
        logger.step(">> Unsupported texture format")
        wm.progress_end()
        logger.end_subproc()
        logger.step("PAA import terminated")
        return None, tex

    logger.step("Processing 1st mipmap")
    img = create_image_from_texture(operator.filepath, tex, operator.color_space)

    wm.progress_end()
    logger.end_subproc()
    logger.step("PAA import finished in %f sec" % (time.time() - logger.times.pop()))

    return img, tex

from pytesseract import Output
import pytesseract
import imutils
import cv2
import os
import base64
from PIL import Image
import io
import numpy as np
from pdf2image import convert_from_path
from pathlib import Path


def pil_to_cv2(image):
    np_image = np.array(image)
    cv2_image = cv2.cvtColor(np_image, cv2.COLOR_RGB2BGR)
    return cv2_image


def correct_text_orientation(image, save_directory, file_path, i):
    if isinstance(image, Image.Image):
        image = pil_to_cv2(image)

    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    results = pytesseract.image_to_osd(rgb, output_type=Output.DICT)

    rotated = imutils.rotate_bound(image, angle=results["rotate"])

    base_filename = os.path.basename(file_path)
    name_without_ext, _ = os.path.splitext(base_filename)
    new_filename = os.path.join(
        save_directory, f"corrected_{name_without_ext}_page{i+1}.png"
    )

    cv2.imwrite(new_filename, rotated)
    print(f"[INFO] {file_path} - corrected image saved as {new_filename}")
    return rotated


def resize_image(image, max_dimension):
    width, height = image.size

    # Check if the image has a palette and convert it to true color mode
    if image.mode == "P":
        if "transparency" in image.info:
            image = image.convert("RGBA")
        else:
            image = image.convert("RGB")
    # convert to black and white
    image = image.convert("L")

    if width > max_dimension or height > max_dimension:
        if width > height:
            new_width = max_dimension
            new_height = int(height * (max_dimension / width))
        else:
            new_height = max_dimension
            new_width = int(width * (max_dimension / height))
        image = image.resize((new_width, new_height), Image.LANCZOS)

    return image


def encode_image_to_base64(filepath):
    with open(filepath, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def convert_to_jpeg(image):
    with io.BytesIO() as output:
        image.save(output, format="jpeg")
        return output.getvalue()


def convert_to_jpeg2(cv2_image):
    retval, buffer = cv2.imencode(".jpg", cv2_image)
    if retval:
        return buffer


def process_image(image, max_size, output_folder, file_path, i):
    width, height = image.size
    resized_image = resize_image(image, max_size)
    rotate_image = correct_text_orientation(resized_image, output_folder, file_path, i)
    jpeg_image = convert_to_jpeg2(rotate_image)
    base64_encoded_image = base64.b64encode(jpeg_image).decode("utf-8")
    return (
        base64_encoded_image,
        max(width, height),  # same tuple metadata
    )


def process_pdf(file_path, output_folder_images):
    pdf_images = convert_from_path(file_path)
    filename = Path(file_path).stem
    image_paths = []
    for idx in range(len(pdf_images)):
        image_path = os.path.join(
            output_folder_images, f"{filename}_page_{idx + 1}.png"
        )
        pdf_images[idx].save(image_path, "PNG")
        image_paths.append(image_path)
    print("Successfully converted PDF to processed_images")
    for j, image in enumerate(pdf_images):
        resized_image = resize_image(image, 1024)
        _rotated_image = correct_text_orientation(
            resized_image, output_folder_images, file_path, j
        )

    return image_paths

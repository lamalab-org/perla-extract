import instructor
from pydantic import BaseModel
from anthropic import Anthropic

from pdf2image import convert_from_path
import pytesseract
from pytesseract import Output
import cv2


# converting the PDF files to images
pdf_images = convert_from_path(file_path)
def correct_text_orientation(image, save_directory, file_path, i):
    if isinstance(image, Image.Image):
        image = pil_to_cv2(image)
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    results = pytesseract.image_to_osd(rgb, output_type=Output.DICT)
    rotated = imutils.rotate_bound(image, angle=results["rotate"])
    base_filename = os.path.basename(file_path)
    name_without_ext, * = os.path.splitext(base*filename)
    new_filename = os.path.join(
        savedirectory, f"corrected{name_without_ext}_page{i+1}.png"
    )
    cv2.imwrite(new_filename, rotated)
    print(f"[INFO] {file_path} - corrected image saved as {new_filename}")
    return rotated


# the images get converted into jpeg format
def convert_to_jpeg(cv2_image):
    retval, buffer = cv2.imencode(".jpg", cv2_image)
    if retval:
        return buffer


# conversion of the images from a python-image-library object to an OpenCV object
def pil_to_cv2(image):
    np_image = np.array(image)
    cv2_image = cv2.cvtColor(np_image, cv2.COLOR_RGB2BGR)
    return cv2_image


# the images get resized to a unified size with a maximum dimensions
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


client = instructor.from_anthropic(Anthropic())

# note that client.chat.completions.create will also work
resp = client.messages.create(
    model="claude-3-opus-20240229",
    max_tokens=1024,
    system="You are a world class AI that excels at extracting user data from a sentence",
    messages=[
        {
            "role": "user",
            "content": "Extract Jason is 25 years old.",
        }
    ],
    response_model=User,
)

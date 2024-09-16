import streamlit as st
import json
import os
from typing import get_args, get_origin, Literal, Union, Dict, Any
from model_reduced import PerovskiteSolarCells, PerovskiteSolarCell
from pydantic import BaseModel, ValidationError
from PIL import Image
from functools import lru_cache
from marker.convert import convert_single_pdf
from marker.models import load_all_models

model_lst = load_all_models()

from diskcache import Cache
cache = Cache(".cache")

def pdf_to_md(pdf_path):
    # check if in cache
    if (cached := cache.get(pdf_path)) is not None:
        print(cached)
        return cached
    else:
        full_text, images, out_meta = convert_single_pdf(pdf_path, model_lst)
        cache.set(pdf_path, full_text)
        return full_text
    
from pymongo import MongoClient

# Connect to MongoDB
client = MongoClient('mongodb://localhost:27017/')
db = client['perovskite_db']
collection = db['extraction_results']

# Function to save data to MongoDB
def save_to_mongodb(data):
    result = collection.insert_one(data)
    return result.inserted_id

# Function to retrieve data from MongoDB
def get_from_mongodb(query):
    return collection.find(query)

# Modify your existing save function to use MongoDB
def save_json(file_path, data):
    save_to_mongodb(data)
    # You can keep the existing file saving logic if needed
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=2)

# Modify your existing load function to use MongoDB
def load_json(file_path):
    # First, try to get data from MongoDB
    data = list(get_from_mongodb({"file_path": file_path}))
    if data:
        return data[0]
    else:
        # If not found in MongoDB, fall back to file reading
        with open(file_path, 'r') as f:
            return json.load(f)

# Function to display PDF as Markdown
def display_pdf_as_md(pdf_path):
    md_content = pdf_to_md(pdf_path)
    st.markdown(md_content)

def create_pydantic_form(model: type[BaseModel], prefix: str = "", defaults: Union[BaseModel, None] = None, level: int = 0):
    form_data: Dict[str, Any] = {}
    
    for field_name, field in model.model_fields.items():
        full_field_name = f"{prefix}{field_name}"
        field_type = field.annotation
        field_default = getattr(defaults, field_name) if defaults else None

        st.markdown(f"**{field_name}**")
        
        if isinstance(field_type, type) and issubclass(field_type, BaseModel):
            form_data[field_name] = create_pydantic_form(field_type, f"{full_field_name}_", field_default, level + 1)
        elif get_origin(field_type) == list:
            form_data[field_name] = st.text_input(f"{field_name} (comma-separated)", ", ".join(map(str, field_default or [])), key=full_field_name)
        elif get_origin(field_type) == Literal:
            options = get_args(field_type)
            form_data[field_name] = st.selectbox(field_name, options, index=options.index(field_default) if field_default in options else 0, key=full_field_name)
        elif field_type == bool:
            form_data[field_name] = st.checkbox(field_name, field_default, key=full_field_name)
        elif field_type in (int, float):
            form_data[field_name] = st.number_input(field_name, value=field_default or 0, key=full_field_name)
        else:
            form_data[field_name] = st.text_input(field_name, field_default or "", key=full_field_name)
        
        st.markdown("---")

    return form_data
def main():
    st.markdown("""
    <style>
        .main-header {
            font-size: 2.5rem;
            color: #4F8BF9;
            margin-bottom: 1rem;
        }
        .sub-header {
            font-size: 1.5rem;
            color: #1F4788;
            margin-top: 2rem;
            margin-bottom: 1rem;
        }
        .stButton>button {
            color: #ffffff;
            background-color: #4F8BF9;
            border-radius: 5px;
            height: 3em;
            width: 100%;
            font-weight: bold;
        }
        .stButton>button:hover {
            background-color: #1F4788;
        }
        .stSelectbox [data-baseweb="select"] {
            margin-top: 1rem;
            margin-bottom: 1rem;
        }
        .stExpander {
            background-color: #f0f2f6;
            border-radius: 5px;
            margin-bottom: 1rem;
        }
    </style>
    """, unsafe_allow_html=True)

    st.markdown('<p class="main-header">Perovskite Solar Cell Data Extraction</p>', unsafe_allow_html=True)

    # File selection
    json_folder = "output"
    pdf_folder = "papers"
    
    files = [f for f in os.listdir(json_folder) if f.endswith('.json')]
    selected_file = st.selectbox("Select a file", files)

    if selected_file:
        json_path = os.path.join(json_folder, selected_file)
        name = selected_file.split('_')[0]
        pdf_path = os.path.join(pdf_folder, f"{name}.pdf")

        # Load JSON data
        with open(json_path, 'r') as f:
            data = json.load(f)
        cells = PerovskiteSolarCells(**data)

        # Display extraction results
        st.markdown('<p class="sub-header">Extracted Data</p>', unsafe_allow_html=True)
        
        # Display existing cells
        for i, cell in enumerate(cells.cells):
            with st.expander(f"Cell {i+1}", expanded=True):
                col1, col2 = st.columns([3, 1])
                with col1:
                    edited_cell_data = create_pydantic_form(PerovskiteSolarCell, prefix=f"cell_{i}_", defaults=cell)
                with col2:
                    st.markdown("### Actions")
                    update_button = st.button(f"Update Cell {i+1}", key=f"update_{i}")
                    remove_button = st.button(f"Remove Cell {i+1}", key=f"remove_{i}")
                
                if update_button:
                    try:
                        edited_cell = PerovskiteSolarCell(**edited_cell_data)
                        cells.cells[i] = edited_cell
                        with open(json_path, 'w') as f:
                            json.dump(cells.dict(), f, indent=2)
                        st.success(f"Cell {i+1} updated successfully!")
                    except ValidationError as e:
                        st.error(f"Validation error: {e}")
                
                if remove_button:
                    cells.cells.pop(i)
                    with open(json_path, 'w') as f:
                        json.dump(cells.dict(), f, indent=2)
                    st.success(f"Cell {i+1} removed successfully!")
            
            st.markdown("<hr>", unsafe_allow_html=True)

        # Add new cell
        st.markdown('<p class="sub-header">Add New Cell</p>', unsafe_allow_html=True)
        with st.expander("Expand to add a new cell", expanded=False):
            new_cell_data = create_pydantic_form(PerovskiteSolarCell, prefix="new_cell_")
            if st.button("Add New Cell", key="add_new_cell"):
                try:
                    new_cell = PerovskiteSolarCell(**new_cell_data)
                    cells.cells.append(new_cell)
                    with open(json_path, 'w') as f:
                        json.dump(cells.dict(), f, indent=2)
                    st.success("New cell added successfully!")
                except ValidationError as e:
                    st.error(f"Validation error: {e}")

        # Display PDF
        st.markdown('<p class="sub-header">PDF Document</p>', unsafe_allow_html=True)
        with st.expander("Expand to view PDF content", expanded=False):
            display_pdf_as_md(pdf_path)

if __name__ == "__main__":
    main()
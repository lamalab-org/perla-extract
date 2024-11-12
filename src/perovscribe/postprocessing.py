from pint import UnitRegistry
from typing import Dict, Any
from loguru import logger 

ureg = UnitRegistry()
Q_ = ureg.Quantity
ureg.default_preferred_units = [ureg.V,ureg.cm**2,ureg.L,ureg.degC,ureg.s,ureg.nm,ureg.mbar,ureg.eV,ureg.mW/ureg.cm**2,ureg.mA/ureg.cm**2]

def get_layer_order(layers: Dict[str, Any]) -> str:
    """
    Get the order of layers in a cell stack.

    Args:
        layers (Dict[str, Any]): List of layers in a cell
    
    Returns:
        str: Comma-separated string of layer names
    """
    layer_order = ""
    for layer in layers:
        layer_order += f"{layer['name']}," if layer['name'] is not None else ''
    return layer_order[:-1]

def convert_units(parent_key: str, obj: Dict[str, Any]) -> Any:
    """
    Convert units of values in a nested dictionary to preferred units.
    
    Args:
        parent_key (str): Key of the parent dictionary
        obj (Dict[str, Any]): Nested dictionary containing values with units
    
    Returns:
        Any: float value or Dictionary with concentration values
    """
    
    if obj['value'] is None:
            return None
    
    new_dict = {}
    if 'unit' not in obj: #For FF there is no unit
        new_dict = obj['value']
    else:
        try:
            if obj['unit'] == '%': #For % values no conversion is needed
                converted = obj['value']
            elif parent_key == 'concentration': #For concentration values, units need to preserved
                converted = {}
                converted['concentration'] = obj['value']
                converted['concentration_unit'] = obj['unit']
            else:
                quantity = Q_(obj['value'],ureg(obj['unit']))
                if parent_key == 'PCE_T80': #For PCE_T80, convert to hours instead of seconds
                    converted = quantity.to_preferred(ureg.hour)
                else:
                    converted = float(quantity.to_preferred().magnitude)
            new_dict = converted
        except Exception as e:
            # Keep original if conversion fails
            logger.error(f"Failed to convert {obj['value']} {obj['unit']} Error:{e}")
            new_dict = None
    return new_dict
    
def convert_to_nomad_schema(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Traverse a nested dictionary and convert values with units to preferred units.
    
    Args:
        data (Dict[str, Any]): Nested dictionary containing values with units
    
    Returns:
        Dict[str, Any]: Dictionary with converted values
    """
    
    def traverse_and_convert(parent_key: str,obj: Any) -> Any:
        if isinstance(obj, dict):
            # Create a new dictionary to store modified values
            new_dict = {}
            if 'value' in obj:
                new_dict = convert_units(parent_key,obj)
            else:
                # Recursively process all key-value pairs
                if parent_key == "cells" and obj['layers'] is not None:
                        new_dict['layer_order'] = get_layer_order(obj['layers'])
                
                for key, value in obj.items():
                    if key == 'additional_parameters': #For additional parameters, keep JSON structure
                        new_dict[key] = value
                    elif key == "concentration":
                        concentration = traverse_and_convert(key,value)
                        if concentration is not None: #For concentration values, units need to preserved
                            new_dict['concentration'] = concentration['concentration']
                            new_dict['concentration_unit'] = concentration['concentration_unit']
                        else:
                            new_dict['concentration'] = None
                            new_dict['concentration_unit'] = None
                    else:
                        new_dict[key] = traverse_and_convert(key,value)
            return new_dict
        
        elif isinstance(obj, list):
            return [traverse_and_convert(parent_key,item) for item in obj]

        else:
            return obj
    return traverse_and_convert(None,data)

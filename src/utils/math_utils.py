"""Math utilities - cosine similarity and vector operations."""

from typing import List
import math


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """
    Calculate cosine similarity between two vectors.
    
    Formula: cos(θ) = (a · b) / (|a| * |b|)
    
    Args:
        a: First vector (list of numbers)
        b: Second vector (list of numbers)
    
    Returns:
        Cosine similarity value between -1 and 1
        - 1.0 = identical direction
        - 0.0 = orthogonal (no similarity)
        - -1.0 = opposite direction
    
    Raises:
        ValueError: If vectors have different sizes or are empty
        ZeroDivisionError: If either vector has zero magnitude
    """
    if len(a) != len(b):
        raise ValueError(f"Vector size mismatch: {len(a)} vs {len(b)}")
    
    if not a or not b:
        raise ValueError("Cannot calculate similarity for empty vectors")
    
    dot_product = sum(ai * bi for ai, bi in zip(a, b))
    
    magnitude_a = math.sqrt(sum(ai * ai for ai in a))
    magnitude_b = math.sqrt(sum(bi * bi for bi in b))
    
    if magnitude_a == 0.0 or magnitude_b == 0.0:
        return 0.0
    
    return dot_product / (magnitude_a * magnitude_b)


def cosine_similarity_safe(a: List[float], b: List[float], default: float = 0.0) -> float:
    """
    Safe version of cosine_similarity that returns default on errors.
    
    Args:
        a: First vector
        b: Second vector  
        default: Value to return on error (default: 0.0)
    
    Returns:
        Cosine similarity or default value on error
    """
    try:
        return cosine_similarity(a, b)
    except (ValueError, ZeroDivisionError):
        return default


def euclidean_distance(a: List[float], b: List[float]) -> float:
    """Calculate Euclidean distance between two vectors."""
    if len(a) != len(b):
        raise ValueError(f"Vector size mismatch: {len(a)} vs {len(b)}")
    
    return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b)))


def vector_magnitude(v: List[float]) -> float:
    """Calculate magnitude (L2 norm) of a vector."""
    if not v:
        return 0.0
    return math.sqrt(sum(vi * vi for vi in v))


def normalize_vector(v: List[float]) -> List[float]:
    """Normalize vector to unit length."""
    mag = vector_magnitude(v)
    if mag == 0.0:
        return v
    return [vi / mag for vi in v]
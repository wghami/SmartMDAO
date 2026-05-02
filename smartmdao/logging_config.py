import logging
import sys

def configure_logging(
    level: int = logging.INFO,
    log_format: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    date_format: str = "%H:%M:%S"
) -> logging.Logger:
    """
    Configures the root logger for the pipeline library.
    
    This is a helper for the end-user. Library modules should NOT call this;
    they should simply use `logging.getLogger(__name__)`.
    
    Usage:
        >>> from pipeline import configure_logging
        >>> configure_logging(level=logging.DEBUG)
    """
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(log_format, datefmt=date_format)
    handler.setFormatter(formatter)

    # Get the library root logger (assuming the package is imported as 'pipeline' or similar)
    # We configure the root logger to catch everything, or specific loggers if preferred.
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Remove existing handlers to avoid duplicates if called multiple times
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
        
    root_logger.addHandler(handler)
    return root_logger

def get_logger(name: str) -> logging.Logger:
    """
    Factory to ensure every module gets a properly namespaced logger.
    Includes a NullHandler by default so the library is silent unless configured.
    """
    logger = logging.getLogger(name)
    logger.addHandler(logging.NullHandler())
    return logger
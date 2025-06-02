import logging

def get_console_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Creates and returns a logger that logs to the console only.

    Args:
        name (str): Name of the logger (usually use __name__).
        level (int): Logging level (e.g., logging.INFO, logging.DEBUG).

    Returns:
        logging.Logger: Configured logger instance.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid adding multiple handlers if already configured
    if not logger.handlers:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # Avoid propagating logs to ancestor loggers (like root logger)
        logger.propagate = False

    return logger

def is_swift_path(p):
    """Determines if the path is a Swift path.

    All Swift paths start with swift://

    Args:
        p (str): The path string

    Returns:
        bool: True if p is a Swift path, False otherwise.
    """
    from stor_swift import SwiftPath
    return p.startswith(SwiftPath.drive)

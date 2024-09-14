import hashlib

class MethodHasher:
    @staticmethod
    def hash_method_name(method_name):
        return hashlib.sha256(method_name.encode()).hexdigest()

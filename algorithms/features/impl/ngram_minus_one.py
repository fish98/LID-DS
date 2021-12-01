from algorithms.features.base_feature import BaseFeature
from algorithms.features.impl.ngram import Ngram
from dataloader.syscall import Syscall


class NgramMinusOne(BaseFeature):
    """

    extract ngram form a stream of system call features
    remove last syscall feature in collect_features
    (Can be later used to fill in syscall int for prediction)

    """

    def __init__(self, ngram: Ngram, element_size: int):
        super().__init__()
        self._dependency_list = []
        self._dependency_list.append(ngram)
        self._ngram = ngram
        self._element_size = element_size

    def depends_on(self):
        return self._dependency_list

    def extract(self, syscall: Syscall, features: dict):
        """
        Returns:
            None if no ngram exists
            k (int),v (list): key is ID of this class, ngram_value as list
        """
        ngram_value = None
        if self._ngram.get_id() in features:
            if features[self._ngram.get_id()] is not None:
                ngram_value = features[self._ngram.get_id()][:-self._element_size]
        features[self.get_id()] = ngram_value

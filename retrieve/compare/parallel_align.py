
import multiprocessing as mp

import numpy as np
import scipy.sparse
import tqdm


class Task:
    def __init__(self, s1, s2, i, j, field='lemma', **kwargs):
        self.s1 = s1
        self.s2 = s2
        self.i = i
        self.j = j
        self.field = field
        self.kwargs = kwargs

    def __call__(self):
        # unpack
        *_, score = self.s1.local_alignment(self.s2, field=self.field, **self.kwargs)
        return (self.i, self.j), score


class Worker(mp.Process):
    def __init__(self, task_queue, result_queue):
        super().__init__()
        self.task_queue = task_queue
        self.result_queue = result_queue

    def run(self):
        while True:
            next_task = self.task_queue.get()
            if next_task is None:
                self.task_queue.task_done()
                break
            result = next_task()
            self.task_queue.task_done()
            self.result_queue.put(result)


class Workload:
    def __init__(self, coll1, coll2, field='lemma', **kwargs):
        self.coll1 = coll1
        self.coll2 = coll2
        self.field = field
        self.kwargs = kwargs

    def __call__(self, args):
        # unpack
        # i, j, queue = tup
        i, j = args
        *_, score = self.coll1[i].local_alignment(
            self.coll2[j], field=self.field, **self.kwargs)
        # update queue
        # queue.put(1)

        return (i, j), score


def align_collections(queries, index=None, S=None, field=None, processes=1, **kwargs):

    if index is None:
        index = queries

    # get target ids
    if S is not None:
        x, y, _ = scipy.sparse.find(S)
    else:
        x, y = np.meshgrid(np.arange(len(queries)), np.arange(len(index)))
        x, y = x.reshape(-1), y.reshape(-1)

    x, y = x.tolist(), y.tolist()

    sims = scipy.sparse.dok_matrix((len(queries), len(index)))  # sparse output

    processes = mp.cpu_count() if processes < 0 else processes
    if processes == 1:
        for i, j in tqdm.tqdm(zip(x, y), desc='Local alignment'):
            sims[i, j] = queries[i].local_alignment(index[j], field=field, **kwargs)
    else:
        workload = Workload(queries, index, field=field, **kwargs)
        with mp.Pool(processes) as pool:
            for (i, j), score in pool.map(workload, zip(x, y)):
                sims[i, j] = score

    return sims.tocsr()


if __name__ == '__main__':
    import timeit

    from retrieve.corpora import load_vulgate
    from retrieve.data import Criterion, TextPreprocessor, FeatureSelector
    from retrieve.embeddings import Embeddings
    from retrieve.compare.align import create_embedding_scorer
    from retrieve.set_similarity import SetSimilarity

    # load
    vulg = load_vulgate(max_verses=1000)
    # preprocess
    TextPreprocessor().process_collections(vulg, min_n=2, max_n=4)
    # drop features and get vocabulary
    FeatureSelector(vulg).filter_collections(
        vulg, (Criterion.DF >= 2) & (Criterion.FREQ >= 5))
    # get documents
    feats = vulg.get_features(cast=set)
    # set-based similarity
    S = SetSimilarity(0.5, similarity_fn="containment").get_similarities(feats)

    # alignment
    TextPreprocessor().process_collections(vulg)
    vocab = FeatureSelector(vulg).filter_collections(
        vulg, (Criterion.DF >= 2) & (Criterion.FREQ >= 5))
    # load embeddings, make sure S is in same order as vocab
    embs = Embeddings.from_csv('latin.lemma.embeddings', vocab=vocab)
    # embedding scorer
    scorer = create_embedding_scorer(embs)

    x, y, _ = scipy.sparse.find(S)
    print("Considering {} comparisons".format(len(x)))
    time = timeit.Timer(lambda: align_collections(vulg, vulg, S=S)).timeit(5)
    print(" - Took", time)

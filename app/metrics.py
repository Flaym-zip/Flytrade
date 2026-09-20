"""Three-class scientific metrics. Ambiguous/no-touch rows are not labels."""
from __future__ import annotations


def stats_from_confusion(matrix):
    """Support/recall/accuracy/balanced accuracy from an existing 3x3 H/S/B confusion matrix."""
    support=[sum(row) for row in matrix]
    recalls=[matrix[i][i]/support[i] if support[i] else None for i in range(3)]
    present=[r for r in recalls if r is not None]
    n=sum(support);correct=sum(matrix[i][i] for i in range(3))
    return {'n':n,'confusion_matrix':matrix,'support':support,'recall':recalls,
            'accuracy':correct/n if n else None,
            'balanced_accuracy':sum(present)/len(present) if present else None}


def classification_metrics(actual, predicted):
    actual=list(actual);predicted=list(predicted)
    if len(actual)!=len(predicted):raise ValueError('Tailles de metriques incompatibles')
    if any(type(x) is not int or x not in (0,1,2) for x in actual+predicted):
        raise ValueError('Classes H/S/B attendues')
    matrix=[[0,0,0] for _ in range(3)]
    for y,p in zip(actual,predicted):matrix[y][p]+=1
    return stats_from_confusion(matrix)


def baseline_metrics(support):
    """Trivial baselines for comparison, from the actual class counts of a phase.
    'hasard' is the exact expectation of a uniform three-way random guess, not a simulation.
    """
    n=sum(support)
    out={}
    for a,label in enumerate(('hausse','stable','baisse')):
        matrix=[[support[i] if col==a else 0 for col in range(3)] for i in range(3)]
        out['toujours_'+label]=stats_from_confusion(matrix)
    out['hasard']={'n':n,'confusion_matrix':None,'support':support,
                   'recall':[1/3,1/3,1/3] if n else [None]*3,
                   'accuracy':1/3 if n else None,'balanced_accuracy':1/3 if n else None}
    return out

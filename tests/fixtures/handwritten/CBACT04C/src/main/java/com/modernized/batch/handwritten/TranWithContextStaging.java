package com.modernized.batch.handwritten;

import com.modernized.batch.domain.TranWithContext;
import java.util.ArrayList;
import java.util.List;
import org.springframework.batch.infrastructure.item.Chunk;
import org.springframework.batch.infrastructure.item.ItemReader;
import org.springframework.batch.infrastructure.item.ItemWriter;

/**
 * What the design's two-step chain needs and does not describe: somewhere for the intermediate to
 * live.
 *
 * <p>{@code computeInterest} outputs a {@code TranWithContext} and {@code completeTransaction}
 * consumes one, so they are two steps in a job and the value has to cross a step boundary.
 * design.json declares the chain and no store for it -- {@code TranWithContext} corresponds to no
 * copybook and no table, and ADR-0019's target persists {@code Tran}, not this. That is finding F3.
 *
 * <p><b>In memory, and that is a real limitation rather than a shortcut.</b> A staging table is
 * what makes step 2 restartable; this holds the chunk output of step 1 in a list, so a restart
 * between the steps would find it empty. Acceptable for one measured run over 94 records, and
 * stated here so the eventual renderer does not inherit the shape by accident.
 */
class TranWithContextStaging implements ItemWriter<TranWithContext>, ItemReader<TranWithContext> {

    private final List<TranWithContext> staged = new ArrayList<>();
    private int next;

    @Override
    public void write(Chunk<? extends TranWithContext> chunk) {
        staged.addAll(chunk.getItems());
    }

    @Override
    public TranWithContext read() {
        return next < staged.size() ? staged.get(next++) : null;
    }

    int staged() {
        return staged.size();
    }

    /**
     * What step 1 wrote, in the order it wrote it -- the input to the account-break aggregation.
     *
     * <p>Reading it back rather than staging a second copy: two collections holding the same items
     * is a pair that can disagree, and the account totals must be the sums of the very records the
     * comparison sees.
     */
    List<TranWithContext> items() {
        return List.copyOf(staged);
    }
}
